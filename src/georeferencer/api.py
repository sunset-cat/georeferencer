import logging
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote, urlparse
from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI, Request, UploadFile, File, BackgroundTasks, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import Field, HttpUrl
from typing import Literal

from .config import Settings
from .extraction import OpenAIExtractor
from .models import Model, RunResult
from .pipeline import Pipeline
from .observability import RunContext, diagnostic
from .library import LibraryService
from .pricing import with_cost

logger = logging.getLogger("georeferencer")


class GeoreferenceRequest(Model):
    name: str = Field(default="document", max_length=200)
    content: str = Field(min_length=1, max_length=100_000)
    format: Literal["text", "html"] = "text"
    include_context: bool = False


class URLImport(Model):
    url: HttpUrl


def create_app(pipeline: Pipeline | None = None):
    settings = Settings.from_env()
    pipeline = pipeline or Pipeline(settings, OpenAIExtractor(settings))
    library = LibraryService(pipeline)

    @asynccontextmanager
    async def lifespan(app):
        library.store.recover()
        from .default_documents import seed_defaults
        seed_defaults(library.store)
        yield

    app = FastAPI(title="Georeferencer", version="0.1.0", lifespan=lifespan)
    app.state.library = library
    static = Path(__file__).parent / "static"
    app.mount("/assets", StaticFiles(directory=static), name="assets")

    @app.get("/", include_in_schema=False)
    async def home():
        return FileResponse(static / "index.html")

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon():
        return FileResponse(static / "favicon.svg", media_type="image/svg+xml")

    @app.get("/.well-known/appspecific/com.chrome.devtools.json", include_in_schema=False)
    async def devtools_probe():
        return Response(status_code=204)

    @app.get("/capabilities")
    async def capabilities():
        return {"model_configured": bool(pipeline.settings.model),
                "model": pipeline.settings.model,
                "reasoning_effort": pipeline.settings.reasoning_effort,
                "web_search_model": pipeline.settings.web_search_model or pipeline.settings.model,
                "verification_model": pipeline.settings.verification_model,
                "geocoder_configured": bool(pipeline.settings.geocoder_url),
                "geocoder_provider": pipeline.settings.geocoder_provider,
                "web_search_enabled": pipeline.settings.web_search_enabled,
                "max_web_requests": pipeline.settings.max_web_requests,
                "discovery_scope": "semantic_unit",
                "discovery_max_tool_calls": 2,
                "verification_batch_size": 6,
                "max_verification_requests": pipeline.settings.max_verification_requests,
                "geographic_audit_enabled": pipeline.settings.geographic_audit_enabled,
                "max_geographic_audit_requests": pipeline.settings.max_geographic_audit_requests,
                "geocoder_refinement_enabled": pipeline.settings.geocoder_refinement_enabled,
                "max_geocoder_refinement_requests": pipeline.settings.max_geocoder_refinement_requests,
                "max_run_cost_usd": pipeline.settings.max_run_cost_usd,
                "api_key_configured": bool(os.getenv("OPENAI_API_KEY")),
                "fixture_mode": not isinstance(pipeline.extractor, OpenAIExtractor)}

    def document(identifier):
        try:
            return library.store.get(identifier)
        except KeyError:
            raise HTTPException(404, "Document not found")

    @app.get("/library")
    async def list_documents():
        return library.store.list()

    @app.post('/library/{identifier}/continue',status_code=202)
    async def continue_document(identifier: str, background: BackgroundTasks):
        row = document(identifier)
        if not row['result']:
            raise HTTPException(409,'No saved run to continue')
        if not library.store.claim_retry(identifier):
            raise HTTPException(409,'Document is already processing')
        background.add_task(library.process,identifier,resume=True)
        return {'id':identifier,'status':'processing','resuming':True}

    @app.post('/library/{identifier}/revalidate',status_code=202)
    async def revalidate_document(identifier: str, background: BackgroundTasks):
        row=document(identifier)
        if not row['result'] or not row['text']:
            raise HTTPException(409,'No saved evidence to revalidate')
        if not library.store.claim_retry(identifier):
            raise HTTPException(409,'Document is already processing')
        async def refresh_evidence():
            from .revalidation import revalidate
            try:
                previous=RunResult.model_validate_json(row['result'])
                result=await revalidate(pipeline.settings,row,previous)
                library.store.finish(identifier,result)
            except Exception:
                logger.exception('Cached evidence refresh failed for %s',identifier)
                with library.store.connect() as db:
                    db.execute('UPDATE documents SET status=? WHERE id=?',(row['status'],identifier))
        background.add_task(refresh_evidence)
        return {'id':identifier,'status':'processing','paid_requests':False}

    @app.post("/library/upload", status_code=202)
    async def upload_document(background: BackgroundTasks, file: UploadFile = File(...)):
        try:
            data = await file.read(pipeline.settings.max_download_bytes + 1)
        finally:
            await file.close()
        if not data or len(data) > pipeline.settings.max_download_bytes:
            raise HTTPException(413, "Document must be nonempty and at most 10 MB")
        name = (file.filename or "document.txt").replace("\\", "/").rsplit("/", 1)[-1][:200] or "document.txt"
        identifier = library.store.create(name, content=data)
        background.add_task(library.process, identifier)
        return {"id": identifier, "status": "processing"}

    @app.post("/library/url", status_code=202)
    async def import_url(body: URLImport, background: BackgroundTasks):
        url = str(body.url)
        name = Path(urlparse(url).path).name[:200] or urlparse(url).hostname or "downloaded-document"
        identifier = library.store.create(name, source_url=url)
        background.add_task(library.process, identifier)
        return {"id": identifier, "status": "processing"}

    @app.post("/library/demo", status_code=202)
    async def import_demo(background: BackgroundTasks):
        directory = Path(__file__).parent / "demo"
        identifier = library.store.create("Synthetic mining demo.txt", content=(directory / "demo.txt").read_bytes(), source_url="demo://synthetic")
        background.add_task(library.process, identifier, demo=True)
        return {"id": identifier, "status": "processing"}

    @app.get("/library/{identifier}")
    async def get_document(identifier: str):
        row = document(identifier)
        result = with_cost(json.loads(row["result"])) if row["result"] else None
        text = row["text"] or ""
        original_available = row["content"] is not None
        summary = library.store.summary(row)
        summary.pop("content", None)
        summary.pop("text", None)
        return {**summary, "text": text, "original_available": original_available, "result": result}

    @app.post("/library/{identifier}/retry", status_code=202)
    async def retry_document(identifier: str, background: BackgroundTasks):
        row = document(identifier)
        if not library.store.claim_retry(identifier):
            raise HTTPException(409, "This document is already processing")
        background.add_task(library.process, identifier, demo=row["source_url"] == "demo://synthetic")
        return {"id": identifier, "status": "processing"}

    @app.get("/library/{identifier}/runs")
    async def run_history(identifier: str):
        document(identifier)
        runs = library.store.history(identifier)
        for run in runs:
            with_cost(run["result"])
        return runs

    @app.get("/library/{identifier}/events")
    async def run_events(identifier: str):
        row = document(identifier)
        if not row["result"]:
            return []
        result = json.loads(row["result"])
        path = pipeline.settings.log_dir / f"{result['request_id']}.jsonl"
        if not path.is_file():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()[-2000:]]

    @app.get("/library/{identifier}/geojson")
    async def download_geojson(identifier: str):
        row = document(identifier)
        if not row["result"]:
            raise HTTPException(409, "No result available yet")
        return Response(json.dumps(json.loads(row["result"])["geojson"]), media_type="application/geo+json",
                        headers={"Content-Disposition": 'attachment; filename="locations.geojson"'})

    @app.get("/library/{identifier}/original")
    async def original_document(identifier: str):
        row = document(identifier)
        if row["content"] is None:
            raise HTTPException(409, "Document has not been downloaded")
        return Response(row["content"], media_type="application/octet-stream",
                        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(row['name'])}"})

    @app.middleware("http")
    async def request_logging(request: Request, call_next):
        # Generate our own ID: never use user input as a log filename.
        request.state.request_id = uuid4().hex
        trace = RunContext(request.state.request_id, pipeline.settings.log_dir, 0, 0)
        request.state.trace = trace
        start = perf_counter()
        trace.event("http.started", method=request.method, path=request.url.path)
        try:
            response = await call_next(request)
        except Exception as exc:
            issue = trace.error("internal_error", diagnostic(exc), "http")
            response = JSONResponse(status_code=500, content={"request_id": request.state.request_id, "issues": [issue.model_dump()]})
        response.headers["X-Request-ID"] = request.state.request_id
        trace.event("http.completed", status_code=response.status_code, latency_ms=round((perf_counter() - start) * 1000))
        logger.info("http.request request_id=%s method=%s path=%s status=%s latency_ms=%s",
                    request.state.request_id, request.method, request.url.path, response.status_code,
                    round((perf_counter() - start) * 1000))
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Do not echo document text through Pydantic's input field.
        errors = [{"location": list(e["loc"]), "message": e["msg"], "type": e["type"]} for e in exc.errors()]
        logger.error("http.validation request_id=%s errors=%s", request.state.request_id, errors)
        request.state.trace.event("http.validation_error", errors=errors)
        return JSONResponse(status_code=422, content={"request_id": request.state.request_id, "errors": errors})

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.post("/georeference", response_model=RunResult)
    async def georeference(body: GeoreferenceRequest, request: Request):
        result = await pipeline.run(body.name, body.content, body.format, body.include_context, request.state.request_id)
        if result.status == "failed":
            return JSONResponse(status_code=502, content=result.model_dump(mode="json"))
        return result

    return app


app = create_app()
