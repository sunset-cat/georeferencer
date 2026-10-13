import argparse
import asyncio
import json
import logging
from dataclasses import replace
from pathlib import Path

from .config import Settings
from .extraction import FixtureExtractor, OpenAIExtractor
from .pipeline import Pipeline


def main():
    parser = argparse.ArgumentParser(description="Extract and verify geographic entities")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    run.add_argument("document", type=Path)
    run.add_argument("--format", choices=["text", "html"])
    run.add_argument("--extraction-fixture", type=Path, help="Replay supplied extraction; no LLM calls")
    run.add_argument("--registry", type=Path)
    run.add_argument("--output", type=Path, default=Path("output"))
    run.add_argument("--include-context", action="store_true")
    serve = commands.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    commands.add_parser("seed-library", help="Download the curated default corpus without model calls")
    analyze = commands.add_parser("analyze-library", help="Analyze and cache the curated default corpus")
    analyze.add_argument("--force", action="store_true", help="Rebuild reports; valid chunk extractions are still reused")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if args.command == "serve":
        import uvicorn
        uvicorn.run("georeferencer.api:app", host=args.host, port=args.port)
        return
    settings = Settings.from_env()
    if args.command == "seed-library":
        from .default_documents import fetch_defaults
        from .library import LibraryStore
        result = asyncio.run(fetch_defaults(LibraryStore(settings.library_dir), settings))
        print(json.dumps(result, indent=2))
        return
    if args.command == "analyze-library":
        from .default_documents import analyze_defaults
        from .library import LibraryStore
        result = asyncio.run(analyze_defaults(LibraryStore(settings.library_dir), settings, force=args.force))
        print(json.dumps(result, indent=2))
        return
    if args.registry:
        settings = replace(settings, registry_path=args.registry)
    if args.extraction_fixture:
        settings = replace(settings, web_search_enabled=False)
    extractor = FixtureExtractor(args.extraction_fixture) if args.extraction_fixture else OpenAIExtractor(settings)
    try:
        content = args.document.read_text(encoding="utf-8-sig")
        result = asyncio.run(Pipeline(settings, extractor).run(args.document.name, content,
            args.format or ("html" if args.document.suffix.lower() in {".html", ".htm"} else "text"), args.include_context))
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Input/configuration error: {exc}\n")
    args.output.mkdir(parents=True, exist_ok=True)
    report = args.output / f"{result.request_id}.report.json"
    geojson = args.output / f"{result.request_id}.geojson"
    report.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    geojson.write_text(json.dumps(result.geojson, indent=2), encoding="utf-8")
    print(json.dumps({"request_id": result.request_id, "status": result.status,
                      "report": str(report), "geojson": str(geojson), "issues": len(result.issues)}))
    raise SystemExit(1 if result.status == "failed" else 0)
