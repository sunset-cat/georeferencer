"""One-document model experiment; never edits .env or startup defaults."""
import argparse
import asyncio
import json
from dataclasses import replace
from pathlib import Path

from georeferencer.config import Settings
from georeferencer.extraction import OpenAIExtractor, PROMPT_VERSION
from georeferencer.library import LibraryStore, now
from georeferencer.pipeline import Pipeline


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--document", default="default-apuradita-channel-sampling")
    parser.add_argument("--model", default="gpt-6-astra")
    parser.add_argument("--reasoning", default="medium")
    args = parser.parse_args()
    base = Settings.from_env()
    store = LibraryStore(base.library_dir)
    row = store.get(args.document)
    if not row or not row.get("text"):
        raise ValueError("Document needs downloaded text before testing")
    if not store.claim_retry(args.document):
        raise ValueError("Document is already processing")
    directory = Path("output/model-tests")
    directory.mkdir(parents=True, exist_ok=True)
    settings = replace(base, model=args.model, reasoning_effort=args.reasoning,
                       timeout_seconds=180, max_output_tokens=12000, max_model_calls=2,
                       web_search_enabled=False)
    try:
        result = await Pipeline(settings, OpenAIExtractor(settings)).run(row["name"], row["text"], "text", include_context=True)
        before = row.get("result")
        if isinstance(before, str):
            before = json.loads(before)
        path = directory / f"{result.request_id}.comparison.json"
        path.write_text(json.dumps({"document_id": args.document, "prompt_version": PROMPT_VERSION,
            "test": {"model": args.model, "reasoning": args.reasoning, "web_search_enabled": False},
            "baseline": before, "result": result.model_dump(mode="json")}, indent=2), encoding="utf-8")
        if result.status != "failed":
            store.finish(args.document, result)
        else:
            # Keep the previous usable result while retaining failed-run history.
            with store.connect() as db:
                db.execute("UPDATE documents SET status=? WHERE id=?", (row["status"], args.document))
                db.execute("INSERT INTO runs VALUES (?,?,?,?)", (result.request_id,args.document,now(),result.model_dump_json()))
        print(json.dumps({"model": args.model, "reasoning": args.reasoning, "status": result.status,
            "comparison": str(path), "usage": result.usage.model_dump(),
            "entities": [(r.entity.name,r.entity.role,[str(v) for v in r.entity.verification_classes],r.status) for r in result.entities],
            "issues": [(i.code,i.message) for i in result.issues]}, indent=2), flush=True)
    except BaseException:
        with store.connect() as db:
            db.execute("UPDATE documents SET status=? WHERE id=?", (row["status"], args.document))
        raise


if __name__ == "__main__":
    asyncio.run(main())
