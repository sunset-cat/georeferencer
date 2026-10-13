"""Versioned research corpus. Startup seeds references without network/model calls."""
import asyncio
import hashlib
import json
from dataclasses import replace
from pathlib import Path

from .download import download_document
from .ingestion import ingest
from .library import decode_document, now


def catalog():
    return json.loads((Path(__file__).parent / "corpus" / "manifest.json").read_text(encoding="utf-8"))["documents"]


def seed_defaults(store):
    added = 0
    with store.connect() as db:
        for item in catalog():
            identifier = "default-" + item["id"]
            if item.get('evaluation_role')=='negative':
                db.execute("UPDATE documents SET excluded_from_library=1,exclusion_reason=? WHERE id=?",('Database-methodology paper retained as a negative extraction fixture',identifier))
                continue
            existing = db.execute("SELECT source_url FROM documents WHERE id=?", (identifier,)).fetchone()
            if existing and existing["source_url"] != item["url"]:
                # A corrected source link must not retain another document's bytes.
                db.execute("UPDATE documents SET name=?,source_url=?,content=NULL,text=NULL,result=NULL,status='ready',corpus_metadata=? WHERE id=?",
                           (item["title"], item["url"], json.dumps(item), identifier))
            if db.execute("SELECT 1 FROM documents WHERE source_url=?", (item["url"],)).fetchone():
                continue
            format = "pdf" if item["url"].endswith(".pdf") else "html"
            db.execute("INSERT OR IGNORE INTO documents (id,name,format,source_url,status,created_at,updated_at,corpus_metadata) VALUES (?,?,?,?,?,?,?,?)",
                       (identifier, item["title"], format, item["url"], "ready", now(), now(), json.dumps(item)))
            added += 1
    return added


async def fetch_defaults(store, settings):
    """Download originals and extract text; never invoke the LLM or geocoder."""
    seed_defaults(store)
    results = []
    slots = asyncio.Semaphore(3)

    async def fetch(item):
        async with slots:
            with store.connect() as db:
                row = dict(db.execute("SELECT * FROM documents WHERE source_url=?", (item["url"],)).fetchone())
            metadata = {**json.loads(row["corpus_metadata"] or "{}"), **item}
            try:
                data, mime = row["content"], row["content_type"]
                final_url = item["url"]
                if data is None:
                    data, mime, final_url = await download_document(item["url"], settings.max_download_bytes, settings.timeout_seconds, total_timeout=180)
                    store.save_download(row["id"], data, mime)
                content, display_format, pipeline_format = await asyncio.to_thread(decode_document, data, item["url"], mime)
                text = ingest(item["title"], content, pipeline_format).text
                store.save_input(row["id"], data, text, display_format)
                metadata.update(download_status="downloaded", downloaded_at=now(), final_url=final_url,
                                sha256=hashlib.sha256(data).hexdigest(), bytes=len(data), characters=len(text),
                                analysis_supported=len(text) <= settings.max_document_characters,
                                analysis_note="Whole-document analysis limit exceeded; requires chunking" if len(text) > settings.max_document_characters else None)
            except Exception as exc:
                metadata.update(download_status="unavailable", download_error=f"{type(exc).__name__}: {exc}")
            with store.connect() as db:
                db.execute("UPDATE documents SET corpus_metadata=? WHERE id=?", (json.dumps(metadata), row["id"]))
            results.append({"id":row["id"], **metadata})
    await asyncio.gather(*(fetch(item) for item in catalog() if item.get('evaluation_role')!='negative'))
    return sorted(results, key=lambda item:item["id"])


async def analyze_defaults(store, settings, *, force=False):
    """Analyze once and persist full reports. Versioned chunks avoid repeated costs."""
    from .chunked_extraction import ChunkedExtractor, split_text
    from .extraction import OpenAIExtractor, PROMPT_VERSION
    from .pipeline import Pipeline, ROUTING_VERSION
    from .models import TYPOLOGY
    analysis_version = hashlib.sha256(json.dumps([PROMPT_VERSION, ROUTING_VERSION, settings.model, settings.reasoning_effort,
        settings.web_search_model, settings.web_search_reasoning_effort, settings.verification_model,
        settings.verification_reasoning_effort, {str(k):sorted(v) for k,v in TYPOLOGY.items()}, "corpus-analysis-v2"]).encode()).hexdigest()
    results = []
    slots = asyncio.Semaphore(2)

    async def analyze(item):
        async with slots:
            with store.connect() as db:
                row = dict(db.execute("SELECT * FROM documents WHERE source_url=?", (item["url"],)).fetchone())
            metadata = json.loads(row["corpus_metadata"] or "{}")
            if not row["text"]:
                results.append({"id":row["id"], "status":"unavailable", "error":metadata.get("download_error")})
                return
            text_hash = hashlib.sha256(row["text"].encode()).hexdigest()
            if not force and row["result"] and metadata.get("analysis_version") == analysis_version and metadata.get("analyzed_text_sha256") == text_hash:
                results.append({"id":row["id"], "status":"cached"})
                return
            chunks = len(split_text(row["text"]))
            config = replace(settings, max_document_characters=1_000_000, max_model_calls=chunks*2,
                             max_source_calls=40, max_output_tokens=10_000, timeout_seconds=90)
            extractor = ChunkedExtractor(OpenAIExtractor(config), config)
            store.claim_retry(row["id"])
            print(f"Analyzing {item['id']} ({chunks} chunks)", flush=True)
            result = await Pipeline(config, extractor).run(row["name"], row["text"], "text", include_context=True)
            store.finish(row["id"], result)
            metadata.update(analysis_version=analysis_version, analyzed_text_sha256=text_hash, analyzed_at=now(),
                            analysis_supported=True, analysis_note="Saved analysis; long documents extracted in chunks", chunk_count=chunks)
            with store.connect() as db:
                db.execute("UPDATE documents SET corpus_metadata=? WHERE id=?", (json.dumps(metadata), row["id"]))
            summary = {"id":row["id"],"status":result.status,"entities":len(result.entities),
                       "features":len(result.geojson["features"]),"issues":len(result.issues),"usage":result.usage.model_dump()}
            results.append(summary)
            print(json.dumps(summary), flush=True)
    await asyncio.gather(*(analyze(item) for item in catalog() if item.get('evaluation_role')!='negative'))
    return sorted(results, key=lambda item:item["id"])
