"""Bounded long-document extraction with reusable, versioned chunk results."""
import hashlib
import json
from pathlib import Path

from .extraction import PROMPT, PROMPT_VERSION
from .models import Document, Extraction
from .observability import diagnostic
from .validation import evidence_errors
from .chunk_context import build_chunk_context, reference_conflict, merge_update

CHUNK_VERSION = "chunks-v3-forward-context"


def split_text(text, size=14_000, overlap=0):
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            boundary = text.rfind("\n", start + size // 2, end)
            if boundary < 0:
                boundary = text.rfind(" ", start + size // 2, end)
            if boundary >= 0:
                end = boundary + 1
        chunks.append((start, end, text[start:end]))
        if end == len(text):
            break
        start = max(start+1,end-min(overlap,size//4))
    return chunks


class ChunkedExtractor:
    def __init__(self, extractor, settings, chunk_size=14_000):
        self.extractor, self.settings, self.chunk_size = extractor, settings, chunk_size
        self.directory = settings.library_dir / "extraction-cache"
        self.last_run = None

    async def extract(self, document, ctx, feedback):
        digest = hashlib.sha256(document.text.encode()).hexdigest()
        # Pipeline's final evidence check may request correction; chunk corrections
        # were already bounded locally. Return the same evidence for quarantine.
        if self.last_run and self.last_run[0] == ctx.request_id:
            return self.last_run[1].model_copy(deep=True)
        chunks = split_text(document.text, self.chunk_size, overlap=350 if self.chunk_size>=1000 else 0)
        ctx.event("extraction.chunked", chunk_count=len(chunks), chunk_size=self.chunk_size,
                  prompt_version=PROMPT_VERSION, cache_version=CHUNK_VERSION)
        entities = []
        successful = 0
        for index, (start, end, text) in enumerate(chunks):
            ctx.chunk_index, ctx.chunk_range = index + 1, (start, end)
            ctx.previous_extraction = None
            ctx.extraction_context = build_chunk_context(entities,document)
            ctx.event('extraction.chunk_context',chunk=index+1,
                existing_location_ids=[e['id'] for e in ctx.extraction_context['existing_locations']],
                geographic_context_count=len(ctx.extraction_context['geographic_contexts']))
            cache_key = hashlib.sha256(json.dumps([digest, start, end, self.settings.model, self.settings.reasoning_effort, PROMPT, CHUNK_VERSION,ctx.extraction_context],sort_keys=True).encode()).hexdigest()
            path = self.directory / f"{cache_key}.json"
            raw_path = self.directory / f"{cache_key}.raw.json"
            part = Document(name=f"{document.name} [chunk {index+1}/{len(chunks)}]", text=text)
            value = None
            if path.is_file():
                try:
                    value = Extraction.model_validate_json(path.read_text(encoding="utf-8"))
                    ctx.event("extraction.cache_hit", chunk=index + 1, cache_key=cache_key)
                    if raw_path.is_file():
                        try:
                            for record in json.loads(raw_path.read_text(encoding="utf-8")):
                                ctx.raw_llm_responses.append({**record, "served_from_cache": True})
                        except (ValueError, OSError, TypeError):
                            ctx.event("extraction.raw_cache_unavailable", chunk=index + 1)
                except (ValueError, OSError):
                    value = None
            if value is None:
                raw_start = len(ctx.raw_llm_responses)
                local_feedback = []
                for attempt in range(2):
                    ctx.event("extraction.chunk_started", chunk=index + 1, start=start, end=end, attempt=attempt + 1)
                    try:
                        value = await self.extractor.extract(part, ctx, local_feedback)
                        local_feedback = evidence_errors(value, part)
                        if not local_feedback:
                            break
                        ctx.previous_extraction = value.model_dump(mode="json")
                    except Exception as exc:
                        local_feedback = [diagnostic(exc)]
                    ctx.event("extraction.chunk_validation_failed", chunk=index + 1, attempt=attempt + 1, errors=local_feedback)
                if value is not None and not local_feedback:
                    self.directory.mkdir(parents=True, exist_ok=True)
                    temporary = path.with_suffix(f".{ctx.request_id}.tmp")
                    temporary.write_text(value.model_dump_json(), encoding="utf-8")
                    temporary.replace(path)
                    if len(ctx.raw_llm_responses) > raw_start:
                        raw_temporary = raw_path.with_suffix(f".{ctx.request_id}.tmp")
                        raw_temporary.write_text(json.dumps(ctx.raw_llm_responses[raw_start:]), encoding="utf-8")
                        raw_temporary.replace(raw_path)
            if value is None:
                ctx.error("chunk_extraction_failed", f"Chunk {index+1}/{len(chunks)} could not be extracted: {local_feedback[0]}", "extraction", retryable=True)
                continue
            successful += 1
            # Anchor in the full ingested document. Immediate surrounding context
            # disambiguates repeated table/header text without LLM offsets.
            evidence_errors(value, part)
            existing={e.id:e for e in entities}
            remap={e.id:e.id if e.id in existing and not reference_conflict(existing[e.id],e)
                else f'c{index+1}-{e.id}' for e in value.entities}
            for entity in value.entities:
                referenced=existing.get(entity.id)
                conflict=reference_conflict(referenced,entity) if referenced else None
                if conflict:
                    ctx.error('chunk_entity_reference_conflict',conflict,'extraction',entity.id)
                    referenced=None
                if referenced is None:
                    entity.id = f"c{index+1}-{entity.id}"
                entity.project_ids=[remap.get(identifier,identifier) for identifier in entity.project_ids]
                if entity.semantic_unit:entity.semantic_unit=remap.get(entity.semantic_unit,entity.semantic_unit)
                for mention in [*entity.mentions, *(c.evidence for c in entity.coordinate_claims),
                                *(c.crs_evidence for c in entity.coordinate_claims if c.crs_evidence)]:
                    if mention.start is not None:
                        position, finish = start + mention.start, start + mention.end
                        mention.prefix = document.text[max(0, position - 120):position] or None
                        mention.suffix = document.text[finish:finish + 120] or None
                        mention.start, mention.end, mention.document_sha256 = position, finish, digest
                if referenced:
                    merge_update(referenced,entity)
                    ctx.event('extraction.entity_updated',chunk=index+1,entity_id=referenced.id,
                        mentions_added=len(entity.mentions),coordinate_claims_added=len(entity.coordinate_claims))
                else:
                    entities.append(entity)
            ctx.event("extraction.chunk_completed", chunk=index+1, entity_count=len(value.entities))
        ctx.chunk_index = ctx.chunk_range = None
        ctx.extraction_context = None
        if not successful:
            raise ValueError("No document chunks could be extracted")
        # Existing references are merged as each chunk arrives; no additional
        # reconciliation model call is made.
        result = Extraction(entities=entities)
        self.last_run = (ctx.request_id, result.model_copy(deep=True))
        return result
