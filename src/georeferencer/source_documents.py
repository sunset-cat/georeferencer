"""Persistent local snapshots of downloaded verification sources and parsed text."""
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
import time
import httpx
from pathlib import Path
from uuid import uuid4


def atomic_write(path: Path, data: bytes):
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_bytes(data)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@dataclass
class SourceDocument:
    data: bytes
    content_type: str
    final_url: str
    path: Path
    sha256: str
    cache_hit: bool


class SourceDocumentCache:
    def __init__(self, directory: Path):
        self.directory = directory
        self.lock = asyncio.Lock()

    async def get(self, url, max_bytes, timeout, *, downloader, before_download=None):
        # Hash URLs instead of allowing remote filenames to determine local paths.
        folder = self.directory / hashlib.sha256(url.encode()).hexdigest()
        async with self.lock:
            try:
                metadata = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
                filename = metadata["filename"]
                if filename not in {"original.pdf", "original.html", "original.txt"}:
                    raise KeyError("Invalid cached filename")
                path = folder / filename
                if path.stat().st_size > max_bytes:
                    raise ValueError("Cached source exceeds the configured document size limit")
                data = await asyncio.to_thread(path.read_bytes)
                digest = hashlib.sha256(data).hexdigest()
                if metadata["url"] == url and digest == metadata["sha256"]:
                    return SourceDocument(data, metadata["content_type"], metadata["final_url"], path, digest, True)
            except (OSError, KeyError, json.JSONDecodeError):
                pass
            failure = folder / 'failure.json'
            if failure.is_file():
                try:
                    saved = json.loads(failure.read_text(encoding='utf-8'))
                    if time.time()-saved['timestamp']<3600:
                        raise ValueError(f"Source previously returned HTTP {saved['status']}; try another supported source")
                except (KeyError,json.JSONDecodeError,OSError):
                    pass
            if before_download:
                before_download()
            try:
                data, mime, final_url = await downloader(url, max_bytes, timeout, total_timeout=180)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in {403,404,410}:
                    folder.mkdir(parents=True,exist_ok=True)
                    await asyncio.to_thread(atomic_write,failure,json.dumps({'status':exc.response.status_code,'timestamp':time.time()}).encode())
                raise
            if len(data) > max_bytes:
                raise ValueError("Download exceeds the document size limit")
            digest = hashlib.sha256(data).hexdigest()
            folder.mkdir(parents=True, exist_ok=True)
            filename = "original.pdf" if data.startswith(b"%PDF-") else "original.html" if "text/html" in mime else "original.txt"
            path = folder / filename
            metadata = {"url": url, "final_url": final_url, "content_type": mime,
                        "bytes": len(data), "sha256": digest, "filename": filename,
                        "downloaded_at": datetime.now(timezone.utc).isoformat()}
            await asyncio.to_thread(atomic_write, path, data)
            await asyncio.to_thread(atomic_write, folder / "metadata.json", json.dumps(metadata, indent=2).encode())
            return SourceDocument(data, mime, final_url, path, digest, False)

    async def parse(self, document: SourceDocument, max_pdf_pages: int):
        # Parsing is also reused across entities and process restarts. Include the
        # content digest and limit so changed downloads/settings cannot reuse stale text.
        path = document.path.parent / f"parsed-v1-{max_pdf_pages}.json"
        async with self.lock:
            try:
                saved = json.loads(path.read_text(encoding="utf-8"))
                if saved["sha256"] == document.sha256:
                    return (saved["body"], saved["display_format"], saved["format"]), True
            except (OSError, KeyError, json.JSONDecodeError):
                pass
            from .library import decode_document
            parsed = await asyncio.to_thread(decode_document, document.data, document.final_url,
                                             document.content_type, max_pdf_pages=max_pdf_pages)
            saved = {"sha256": document.sha256, "body": parsed[0], "display_format": parsed[1], "format": parsed[2]}
            await asyncio.to_thread(atomic_write, path, json.dumps(saved, ensure_ascii=False).encode())
            return parsed, False
