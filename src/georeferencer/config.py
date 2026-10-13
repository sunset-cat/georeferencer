import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    model: str | None = None
    reasoning_effort: str | None = None
    web_search_model: str | None = None
    web_search_reasoning_effort: str | None = None
    verification_model: str | None = None
    verification_reasoning_effort: str | None = None
    max_verification_requests: int = 12
    max_run_cost_usd: float = 0.50
    max_geocoder_calls: int = 80
    max_download_calls: int = 16
    registry_path: Path | None = None
    geocoder_url: str | None = None
    geocoder_provider: str = "nominatim"
    geocoder_user_agent: str = "georeferencer-poc/0.1"
    log_dir: Path = Path("logs")
    max_source_calls: int = 12
    max_model_calls: int = 2
    max_output_tokens: int = 6000
    timeout_seconds: float = 30
    max_document_characters: int = 100_000
    library_dir: Path = Path("data/library")
    max_download_bytes: int = 10_000_000
    max_source_download_bytes: int = 50_000_000
    max_source_pdf_pages: int = 1000
    source_document_cache_dir: Path = Path("data/source_documents")
    web_search_enabled: bool = False
    max_web_requests: int = 8
    web_cache_seconds: int = 86400
    geographic_audit_enabled: bool = True
    max_geographic_audit_requests: int = 8
    geocoder_refinement_enabled: bool = True
    max_geocoder_refinement_requests: int = 8

    @classmethod
    def from_env(cls):
        # Explicit workspace path; do not discover unrelated parent-directory secrets.
        # Shell environment values take precedence over .env values.
        load_dotenv(dotenv_path=Path.cwd() / ".env", override=False, encoding="utf-8-sig")
        registry = os.getenv("GEO_REGISTRY_PATH")
        return cls(
            model=os.getenv("GEO_MODEL"),
            reasoning_effort=os.getenv("GEO_REASONING_EFFORT") or None,
            web_search_model=os.getenv("GEO_WEB_SEARCH_MODEL", "gpt-6-luna"),
            web_search_reasoning_effort=os.getenv("GEO_WEB_SEARCH_REASONING_EFFORT", "low"),
            verification_model=os.getenv("GEO_VERIFICATION_MODEL", "gpt-6.1-sol"),
            verification_reasoning_effort=os.getenv("GEO_VERIFICATION_REASONING_EFFORT", "low"),
            max_verification_requests=int(os.getenv("GEO_MAX_VERIFICATION_REQUESTS", "12")),
            max_run_cost_usd=float(os.getenv("GEO_MAX_RUN_COST_USD", "0.50")),
            max_geocoder_calls=int(os.getenv("GEO_MAX_GEOCODER_CALLS", "80")),
            max_download_calls=int(os.getenv("GEO_MAX_DOWNLOAD_CALLS", "16")),
            registry_path=Path(registry) if registry else None,
            geocoder_url=os.getenv("GEO_GEOCODER_URL"),
            geocoder_provider=os.getenv("GEO_GEOCODER_PROVIDER", "nominatim"),
            geocoder_user_agent=os.getenv("GEO_GEOCODER_USER_AGENT", "georeferencer-poc/0.1"),
            log_dir=Path(os.getenv("GEO_LOG_DIR", "logs")),
            library_dir=Path(os.getenv("GEO_LIBRARY_DIR", "data/library")),
            max_source_download_bytes=int(os.getenv("GEO_MAX_SOURCE_DOWNLOAD_BYTES", "50000000")),
            max_source_pdf_pages=int(os.getenv("GEO_MAX_SOURCE_PDF_PAGES", "1000")),
            source_document_cache_dir=Path(os.getenv("GEO_SOURCE_DOCUMENT_CACHE_DIR", "data/source_documents")),
            web_search_enabled=os.getenv("GEO_WEB_SEARCH_ENABLED", "true").lower() == "true",
            max_web_requests=int(os.getenv("GEO_MAX_WEB_REQUESTS", "8")),
            geographic_audit_enabled=os.getenv('GEO_GEOGRAPHIC_AUDIT_ENABLED', 'true').lower() == 'true',
            max_geographic_audit_requests=int(os.getenv('GEO_MAX_GEOGRAPHIC_AUDIT_REQUESTS', '8')),
            geocoder_refinement_enabled=os.getenv('GEO_GEOCODER_REFINEMENT_ENABLED', 'true').lower() == 'true',
            max_geocoder_refinement_requests=int(os.getenv('GEO_MAX_GEOCODER_REFINEMENT_REQUESTS', '8')),
        )
