"""One JSONL file per run; no credentials or document bodies in logs."""
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

from .models import Issue, Usage

logger = logging.getLogger("georeferencer")


class RunContext:
    def __init__(self, request_id: str, log_dir: Path, max_source_calls: int, max_model_calls: int):
        self.request_id = request_id
        self.usage = Usage()
        self.issues: list[Issue] = []
        self.previous_extraction: dict | None = None
        self.raw_llm_responses: list[dict] = []
        self.chunk_index: int | None = None
        self.chunk_range: tuple[int, int] | None = None
        self.extraction_context: dict | None = None
        self.shared_verification = {}
        self.project_discovery = None
        self.completed_entity_ids = set()
        self.geocoder_calls = 0
        self.download_calls = 0
        self.max_geocoder_calls = None
        self.max_download_calls = None
        self.max_source_calls = max_source_calls
        self.max_model_calls = max_model_calls
        self.started = perf_counter()
        log_dir.mkdir(parents=True, exist_ok=True)
        self.path = log_dir / f"{request_id}.jsonl"

    def event(self, event: str, **fields):
        record = {"timestamp": datetime.now(timezone.utc).isoformat(), "request_id": self.request_id, "event": event, **fields}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        logger.info("%s", json.dumps(record, default=str))

    def error(self, code: str, message: str, stage: str, entity_id=None, retryable=False):
        issue = Issue(code=code, message=message, stage=stage, entity_id=entity_id, retryable=retryable)
        self.issues.append(issue)
        self.event("error", **issue.model_dump())
        return issue

    def consume(self, kind: str):
        if kind in {"geocoder", "download"}:
            limit = getattr(self, f"max_{kind}_calls")
            if limit is None:
                return self.consume("source")
            count = getattr(self, f"{kind}_calls")
            if count >= limit:
                raise BudgetExceeded(f"{kind} request budget exhausted; remaining work is pending")
            setattr(self,f"{kind}_calls",count+1)
            self.usage.source_calls += 1
            return
        field = "source_calls" if kind == "source" else "model_calls"
        limit = self.max_source_calls if kind == "source" else self.max_model_calls
        if getattr(self.usage, field) >= limit:
            raise BudgetExceeded(f"{kind} call budget exhausted")
        setattr(self.usage, field, getattr(self.usage, field) + 1)


class BudgetExceeded(RuntimeError):
    pass


def diagnostic(exc: Exception) -> str:
    """Useful errors without Pydantic input payloads or API credentials."""
    from pydantic import ValidationError
    if isinstance(exc, ValidationError):
        return json.dumps(exc.errors(include_input=False, include_context=False, include_url=False))[:2000]
    message = str(exc)
    key = os.getenv("OPENAI_API_KEY")
    if key:
        message = message.replace(key, "[redacted]")
    return message[:2000]
