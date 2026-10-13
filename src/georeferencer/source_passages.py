"""Bounded passage retrieval from local source snapshots; no model or network calls."""
import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path

from .ingestion import ingest


def entity_passages(entity, text, *, limit=9000):
    text = " ".join(text.split())
    positions = []
    for name in [entity.name, *entity.aliases]:
        if len(name.strip()) < 3 or name.lower().strip() in {"the project", "the zone", "the deposit"}:
            continue
        pattern = r"(?<!\w)" + r"\s+".join(re.escape(w) for w in name.split()) + r"(?!\w)"
        for match in list(re.finditer(pattern, text, re.I))[:80]:
            start, end = max(0, match.start()-500), min(len(text), match.end()+900)
            passage = text[start:end]
            score = 10 if "°" in passage or ("latitude" in passage.lower() and "longitude" in passage.lower()) else 0
            if re.search(r"\b(?:UTM|easting|northing|collar)\b", passage, re.I):
                score += 8
                start = max(0, match.start()-1800)
                end = min(len(text), match.end()+1800)
            positions.append((score, start, end))
    if not positions:
        return []
    windows = []
    for _, start, end in sorted(positions, key=lambda p: (-p[0], p[1])):
        if any(start < b and end > a for a, b in windows):
            continue
        windows.append((start, end))
        if len(windows) == 3:
            break
    # Include separately scoped datum evidence, without inferring it applies to
    # every coordinate in the report. Sol evaluates that relationship explicitly.
    for match in sorted(re.finditer(r"\b(?:datum|WGS\s*84|SAD\s*69|SIRGAS|UTM)\b", text, re.I),
                        key=lambda m: not bool(re.search(r"zone\s*\d|surveys|coordinates",text[max(0,m.start()-100):m.end()+150],re.I)))[:8]:
        start, end = max(0, match.start()-250), min(len(text), match.end()+350)
        if not any(start < b and end > a for a, b in windows):
            windows.append((start, end))
            if len(windows) >= 6:
                break
    passages, used = [], 0
    for start, end in windows:
        passage = text[start:end][:limit-used]
        if passage:
            passages.append(passage)
            used += len(passage)
        if used >= limit:
            break
    return passages


@lru_cache(maxsize=4)
def snapshots(root, pages, max_bytes, signature):
    sources, seen = [], set()
    for path in sorted(Path(root).glob(f"*/parsed-v1-{pages}.json")):
        try:
            metadata = json.loads((path.parent / "metadata.json").read_text(encoding="utf-8"))
            parsed = json.loads(path.read_text(encoding="utf-8"))
            if parsed["sha256"] != metadata["sha256"] or metadata["sha256"] in seen:
                continue
            original = path.parent / metadata["filename"]
            if original.parent != path.parent or not original.is_file() or original.stat().st_size > max_bytes:
                continue
            if hashlib.sha256(original.read_bytes()).hexdigest() != metadata["sha256"]:
                continue
            text = ingest(metadata["final_url"], parsed["body"], parsed["format"]).text
            digest = hashlib.sha256(text.encode()).hexdigest()
            if digest in seen:
                continue
            seen.add(digest)
            sources.append({"url": metadata["url"], "text": text, "source_sha256": digest})
        except (OSError, ValueError, KeyError):
            continue
    return sources


def cached_sources(settings, entity, *, limit=2):
    paths = sorted(settings.source_document_cache_dir.glob(f"*/parsed-v1-{settings.max_source_pdf_pages}.json"))
    signature = tuple((str(p),p.stat().st_mtime_ns,(p.parent/'metadata.json').stat().st_mtime_ns) for p in paths if (p.parent/'metadata.json').is_file())
    sources = [{**s,"passages":entity_passages(entity,s["text"])} for s in snapshots(str(settings.source_document_cache_dir),
        settings.max_source_pdf_pages,settings.max_source_download_bytes,signature)]
    sources = [s for s in sources if s["passages"]]
    sources.sort(key=lambda s: not any("°" in p or "latitude" in p.lower() for p in s["passages"]))
    return sources[:limit] if limit is not None else sources
