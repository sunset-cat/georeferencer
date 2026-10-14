"""Small persistent document library. Background jobs are local to this process."""
import asyncio
import io
import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from pypdf import PdfReader

from .download import download_document, DownloadConnectionError
from .ingestion import ingest
from .models import RunResult
from .observability import RunContext, diagnostic


def now():
    return datetime.now(timezone.utc).isoformat()


class LibraryStore:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "library.sqlite3"
        # A fresh checkout can browse saved demo results without import commands.
        # Only bootstrap the default library; custom/test stores stay independent.
        snapshot = Path.cwd() / "demo" / "library.sqlite3"
        if (directory.resolve() == (Path.cwd() / "data" / "library").resolve()
                and not self.path.exists() and snapshot.is_file()):
            shutil.copyfile(snapshot, self.path)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, format TEXT NOT NULL,
                    content BLOB, text TEXT, source_url TEXT, status TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, result TEXT);
                CREATE TABLE IF NOT EXISTS runs (
                    request_id TEXT PRIMARY KEY, document_id TEXT NOT NULL,
                    created_at TEXT NOT NULL, result TEXT NOT NULL);
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(documents)")}
            if "content_type" not in columns:
                db.execute("ALTER TABLE documents ADD COLUMN content_type TEXT NOT NULL DEFAULT ''")
            if "corpus_metadata" not in columns:
                db.execute("ALTER TABLE documents ADD COLUMN corpus_metadata TEXT")
            if "excluded_from_library" not in columns:
                db.execute("ALTER TABLE documents ADD COLUMN excluded_from_library INTEGER NOT NULL DEFAULT 0")
                db.execute("ALTER TABLE documents ADD COLUMN exclusion_reason TEXT")

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def create(self, name, format="text", content=None, source_url=None):
        identifier = uuid4().hex
        with self.connect() as db:
            db.execute("INSERT INTO documents (id,name,format,content,text,source_url,status,created_at,updated_at,result) VALUES (?, ?, ?, ?, NULL, ?, 'processing', ?, ?, NULL)",
                       (identifier, name, format, content, source_url, now(), now()))
        return identifier

    def get(self, identifier):
        with self.connect() as db:
            row = db.execute("SELECT * FROM documents WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise KeyError(identifier)
        return dict(row)

    def list(self):
        with self.connect() as db:
            rows = db.execute("SELECT id, name, format, source_url, status, created_at, updated_at, result, corpus_metadata FROM documents WHERE excluded_from_library=0 ORDER BY created_at DESC").fetchall()
        return [self.summary(dict(row)) for row in rows]

    def exclude(self, identifier, reason):
        with self.connect() as db:
            db.execute("UPDATE documents SET excluded_from_library=1,exclusion_reason=?,updated_at=? WHERE id=?", (reason, now(), identifier))

    @staticmethod
    def summary(row):
        row["corpus_metadata"] = json.loads(row["corpus_metadata"]) if row.get("corpus_metadata") else None
        result = json.loads(row.pop("result")) if row.get("result") else None
        row.pop("result", None)
        row.update(entity_count=len(result["entities"]) if result else 0,
                   feature_count=len(result["geojson"]["features"]) if result else 0,
                   issue_count=len(result["issues"]) if result else 0)
        return row

    def save_input(self, identifier, content, text, format):
        with self.connect() as db:
            db.execute("UPDATE documents SET content=?, text=?, format=?, updated_at=? WHERE id=?", (content, text, format, now(), identifier))

    def save_download(self, identifier, content, content_type):
        with self.connect() as db:
            db.execute("UPDATE documents SET content=?, content_type=?, updated_at=? WHERE id=?", (content, content_type, now(), identifier))

    def finish(self, identifier, result):
        if result.estimated_cost is None:
            from .pricing import estimate_cost
            result.estimated_cost = estimate_cost(result.usage, result.raw_llm_responses)
        data = result.model_dump_json()
        with self.connect() as db:
            db.execute("UPDATE documents SET status=?, result=?, updated_at=? WHERE id=?", (result.status, data, now(), identifier))
            db.execute("INSERT INTO runs VALUES (?, ?, ?, ?)", (result.request_id, identifier, now(), data))

    def claim_retry(self, identifier):
        with self.connect() as db:
            return db.execute("UPDATE documents SET status='processing', updated_at=? WHERE id=? AND status != 'processing'", (now(), identifier)).rowcount == 1

    def history(self, identifier):
        with self.connect() as db:
            rows = db.execute("SELECT request_id, created_at, result FROM runs WHERE document_id=? ORDER BY created_at DESC", (identifier,)).fetchall()
        return [{"request_id": row["request_id"], "created_at": row["created_at"], "result": json.loads(row["result"])} for row in rows]

    def recover(self):
        with self.connect() as db:
            db.execute("UPDATE documents SET status='interrupted' WHERE status='processing'")


def decode_document(data: bytes, name: str, content_type: str = "", *, max_pdf_pages: int = 150):
    if data.startswith(b"%PDF-") or name.lower().endswith(".pdf") or "application/pdf" in content_type:
        if not data.startswith(b"%PDF-"):
            if "text/html" in content_type or data.lstrip().lower().startswith((b"<!doctype html", b"<html")):
                raise ValueError("Expected a PDF but the source returned an HTML page, possibly an access-verification or error page")
            raise ValueError("Expected a PDF but the response has no PDF header")
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise ValueError("Encrypted PDFs are not supported")
        if len(reader.pages) > max_pdf_pages:
            raise ValueError(f"PDF exceeds the {max_pdf_pages}-page limit")
        text = "\n\n".join(page.extract_text() or "" for page in reader.pages)
        if not text.strip():
            raise ValueError("PDF has no extractable text; scanned PDFs require OCR")
        return text, "pdf", "text"
    if b"\x00" in data:
        raise ValueError("Unsupported binary document; use UTF-8 text, HTML, or a text-based PDF")
    text = data.decode("utf-8-sig")
    format = "html" if "text/html" in content_type or name.lower().endswith((".html", ".htm")) or text.lstrip().lower().startswith(("<!doctype html", "<html")) else "text"
    return text, format, format


class LibraryService:
    def __init__(self, pipeline):
        self.pipeline = pipeline
        self.store = LibraryStore(pipeline.settings.library_dir)
        self.slots = asyncio.Semaphore(2)

    async def process(self, identifier, demo=False, resume=False):
        async with self.slots:
            request_id = uuid4().hex
            trace = RunContext(request_id, self.pipeline.settings.log_dir, 0, 0)
            trace.event("library.processing", document_id=identifier, demo=demo)
            try:
                row = self.store.get(identifier)
                data = row["content"]
                content_type = row["content_type"]
                if data is None:
                    trace.event("download.request", document_id=identifier, url=row["source_url"])
                    data, content_type, final_url = await download_document(row["source_url"], self.pipeline.settings.max_download_bytes, self.pipeline.settings.timeout_seconds)
                    trace.event("download.response", document_id=identifier, bytes=len(data), final_url=final_url)
                    # Preserve the original even if parsing fails, including its MIME type for retries.
                    self.store.save_download(identifier, data, content_type)
                content, display_format, pipeline_format = await asyncio.to_thread(decode_document, data, row["name"], content_type)
                text = ingest(row["name"], content, pipeline_format).text
                self.store.save_input(identifier, data, text, display_format)
                engine = self.pipeline
                from .extraction import OpenAIExtractor
                if not demo and isinstance(engine.extractor, OpenAIExtractor):
                    # Reuse the canonical text and versioned chunks on explicit retry.
                    from dataclasses import replace
                    from .chunked_extraction import ChunkedExtractor, split_text
                    from .extraction import OpenAIExtractor
                    from .pipeline import Pipeline
                    config = replace(engine.settings, max_document_characters=1_000_000,
                                     max_model_calls=2*len(split_text(text)), max_output_tokens=10_000,
                                     max_source_calls=40, timeout_seconds=90)
                    engine = Pipeline(config, ChunkedExtractor(OpenAIExtractor(config), config))
                    content, pipeline_format = text, "text"
                if demo:
                    from dataclasses import replace
                    from .extraction import FixtureExtractor
                    from .pipeline import Pipeline
                    directory = Path(__file__).parent / "demo"
                    engine = Pipeline(replace(engine.settings, registry_path=directory / "demo.registry.json", web_search_enabled=False, geographic_audit_enabled=False, geocoder_refinement_enabled=False), FixtureExtractor(directory / "demo.extraction.json"))
                previous = RunResult.model_validate_json(row['result']) if resume and row.get('result') else None
                result = await engine.run(row["name"], content, pipeline_format, include_context=True, request_id=request_id, resume=previous)
            except Exception as exc:
                is_connection = isinstance(exc, DownloadConnectionError)
                issue = trace.error("download_connection_error" if is_connection else "document_import_error",
                                    diagnostic(exc), "download" if is_connection else "import", retryable=is_connection)
                result = RunResult(request_id=request_id, status="failed", geojson={"type": "FeatureCollection", "features": []}, entities=[], relationships=[], issues=[issue], usage=trace.usage)
            self.store.finish(identifier, result)
            trace.event("library.completed", document_id=identifier, status=result.status)
