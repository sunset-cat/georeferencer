import asyncio
import json
from dataclasses import replace

from georeferencer.chunked_extraction import ChunkedExtractor, split_text
from georeferencer.config import Settings
from georeferencer.default_documents import catalog, seed_defaults
from georeferencer.library import LibraryStore
from georeferencer.models import Document, Entity, Extraction, Mention
from georeferencer.observability import RunContext
from georeferencer.validation import evidence_errors


def test_default_catalog_is_idempotent_and_preserves_results(tmp_path):
    assert {item["id"] for item in catalog()} == {"bc-rob-claims", "bc-drillhole-database", "west-mcarthur-drilling",
        "apuradita-channel-sampling", "monte-do-carmo-serra-alta", "phoenix-jv-moss-drilling"}
    store = LibraryStore(tmp_path)
    assert seed_defaults(store) == len([item for item in catalog() if item.get('evaluation_role')!='negative'])
    first = store.list()[0]
    with store.connect() as db:
        db.execute("UPDATE documents SET status='partial',text='existing' WHERE id=?", (first["id"],))
    assert seed_defaults(store) == 0
    assert store.get(first["id"])["text"] == "existing"
    assert store.get(first["id"])["status"] == "partial"
    assert len({item["url"] for item in catalog()}) == len(catalog())


def test_chunk_partition_does_not_drop_or_duplicate_characters():
    text = "First paragraph.\n" * 80 + "Last character!"
    chunks = split_text(text, 50)
    assert "".join(chunk[2] for chunk in chunks) == text
    assert chunks[0][0] == 0 and chunks[-1][1] == len(text)
    assert all(end-start <= 50 for start,end,_ in chunks)


class FakeExtractor:
    calls = 0
    async def extract(self, document, ctx, feedback):
        self.calls += 1
        ctx.consume("model")
        return Extraction(entities=[Entity(id="e1", name="Ridge", category="mining", subtype="project",
            role="primary_subject", aliases=[], identifiers=[], country_code="CA", jurisdiction="Alberta",
            mentions=[Mention(quote="Ridge project.")])])


def test_chunk_cache_reuses_extraction_and_anchors_full_document(tmp_path):
    settings = Settings(model="test", library_dir=tmp_path, log_dir=tmp_path)
    document = Document(name="demo", text="North Ridge project.\nSouth Ridge project.")
    delegate = FakeExtractor()
    extractor = ChunkedExtractor(delegate, settings, chunk_size=22)
    first = RunContext("first", tmp_path, 0, 4)
    value = asyncio.run(extractor.extract(document, first, []))
    assert delegate.calls == 2
    assert len(value.entities) == 2  # Same names do not establish identity.
    assert evidence_errors(value, document) == []
    assert [e.mentions[0].start for e in value.entities] == [6,27]
    second = RunContext("second", tmp_path, 0, 4)
    replay = asyncio.run(ChunkedExtractor(delegate, settings, 22).extract(document, second, []))
    assert delegate.calls == 2 and second.usage.model_calls == 0
    assert evidence_errors(replay, document) == []
    assert "extraction.cache_hit" in second.path.read_text()
    other = RunContext("third", tmp_path, 0, 4)
    asyncio.run(ChunkedExtractor(delegate, replace(settings, model="changed"), 22).extract(document, other, []))
    assert delegate.calls == 4


def test_failed_chunk_does_not_erase_successful_chunks(tmp_path):
    class BrokenChunk(FakeExtractor):
        async def extract(self, document, ctx, feedback):
            if "South" in document.text:
                raise TimeoutError("provider unavailable")
            return await super().extract(document, ctx, feedback)
    settings = Settings(model="test", library_dir=tmp_path, log_dir=tmp_path)
    ctx = RunContext("partial", tmp_path, 0, 4)
    document = Document(name="demo", text="North Ridge project.\nSouth Ridge project.")
    extractor = ChunkedExtractor(BrokenChunk(), settings, 22)
    value = asyncio.run(extractor.extract(document, ctx, []))
    assert len(value.entities) == 1
    assert any(issue.code == "chunk_extraction_failed" for issue in ctx.issues)
    assert len(list((tmp_path / "extraction-cache").glob("*.json"))) == 1


def test_raw_chunk_responses_survive_cache_without_model_calls(tmp_path):
    raw = '{ "entities": [{"name": "Ridge", "subtype": "project", "role": "primary_subject"}] }'

    class RecordingExtractor(FakeExtractor):
        async def extract(self, document, ctx, feedback):
            ctx.raw_llm_responses.append({"output_text": raw, "chunk": ctx.chunk_index,
                                          "served_from_cache": False})
            return await super().extract(document, ctx, feedback)

    settings = Settings(model="test", library_dir=tmp_path, log_dir=tmp_path)
    document = Document(name="demo", text="North Ridge project.")
    delegate = RecordingExtractor()
    first = RunContext("raw-first", tmp_path, 0, 4)
    asyncio.run(ChunkedExtractor(delegate, settings).extract(document, first, []))
    second = RunContext("raw-second", tmp_path, 0, 4)
    asyncio.run(ChunkedExtractor(delegate, settings).extract(document, second, []))
    assert delegate.calls == 1 and second.usage.model_calls == 0
    assert first.raw_llm_responses[0]["output_text"] == raw
    assert second.raw_llm_responses == [{"output_text": raw, "chunk": 1, "served_from_cache": True}]
