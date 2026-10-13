# Workflow audit — 11 October 2026

This is a review and proposed repair plan, not an implemented redesign. No paid model calls were made during this audit.

## Evidence reviewed

- All 1,453 JSONL files under `logs/`, including 37 `request.started` events; no malformed lines. Most files are HTTP traces, not extraction runs.
- Current saved results and histories for the six visible default documents.
- Original Monte do Carmo press-release text, saved raw extraction and verification responses, cached 2023 technical report.
- Extraction, chunking, evidence validation, consolidation, routing, source retrieval, coordinate acceptance, caching and UI code.
- The interview statement and the failures discussed in the conversation.

Historical error counts are event counts across repeated runs and older implementations; they are not unique defects or a measurement of current failure rates.

| Logged error family | Events | Interpretation / proposed handling |
| --- | ---: | --- |
| Web budget exhausted | 128 | Document-wide four-call limit starves later entities; schedule work by project/source and expose pending work. |
| Invalid extraction evidence | 117 | Historic quote/context failures plus expressions absent from input; validate against source blocks, expose coverage and repair affected blocks only. |
| Source budget exhausted | 46 | Shared source-call budget mixes unrelated operations; separate geocoder, download and paid-model budgets. |
| Web evidence rejected | 26 | Mixed causes: unsupported coordinates, entity/quote mismatch, candidate URL mismatch; retain typed evidence and report the exact failed gate. |
| Verification budget exhausted | 20 | Twelve per-entity calls cannot cover a multi-hole document; batch source verification and deterministic table lookup. |
| Source download failed | 11 | Includes 403, 404 and former size cap; try another supported source, retry transient errors only. |
| Invalid coordinate evidence | 3 | Preserve original claim and rejection reason; repair only the affected claim. |
| Unresolved location | 3 | Generic historic terminal state; distinguish missing evidence from unattempted or unconverted evidence. |
| Pipeline error | 2 | Missing configuration and category/subtype mismatch; validate configuration up front and remove redundant schema requirements. |
| Document import error | 2 | Connection failure or unsupported binary; clear import state and supported-format guidance. |
| Chunk extraction failed | 2 | Connection failures; resumable chunks and bounded transient retries. |
| Invalid extraction schema | 1 | Incompatible category/subtype; simplify model-facing fields and derive routing centrally. |
| Workflow error | 1 | API connection error; retain progress and surface retryability and missing usage. |
| Source parse failed | 1 | PDF URL served HTML; validate response content and try another verified source. |

## Most important finding: coordinates were in the original input

The July 2022 Monte do Carmo press release is approximately 17,031 characters. Its drill-location table contains 17 collar rows, with columns **UTM N then UTM E**, and a footnote:

> Collar coordinates by GNSS TP-20 UTM Coordinates, Datum: SAD69 / zone 22S.

The first saved extraction identifies all 17 drill IDs, including FSA-221 and FSZ-005, but returns **zero coordinate claims**. The schema accepts decimal latitude/longitude and DMS only. Therefore the pipeline recognises the holes but cannot represent their available projected coordinates, and sends them into external resolution.

FSA-221's input row supplies northing 8,809,745 and easting 820,158. The external report supplies easting 820,158.2 and northing 8,809,745. These support the same collar at different reported precision. This is not a need to discover its location on the web.

The technical report contains additional holes such as FSA-212 through FSA-216. Those are not automatically required entities for a run analysing the press release. Supporting-source discovery must not silently expand the original extraction scope.

## Current coverage

| Latest saved document | Entity records | Resolved | Unresolved | Excluded |
| --- | ---: | ---: | ---: | ---: |
| Monte do Carmo | 41 | 3 | 29 | 9 |
| Phoenix JV / Moss | 14 | 1 | 12 | 1 |
| Apuradita | 11 | 1 | 3 | 7 |
| West McArthur | 12 | 2 | 6 | 4 |
| ROB claims | 55 | 3 | 44 | 8 |
| BC database paper | 5 | 0 | 0 | 5 |

These results come from different prompt/workflow versions. Counts measure record states, not extraction recall or correctness. Monte do Carmo includes two separate Serra Alta deposit records, confirming that identifier-only consolidation misses ordinary cross-chunk duplicates.

## Repair plan

### 1. Use coordinate evidence in the input first

Represent geographic, projected and local-grid claims separately. A projected claim must retain easting, northing, units, datum, projection, zone, hemisphere, row evidence and separately scoped CRS evidence. Preserve elevation without confusing it with a horizontal coordinate.

Detect coordinate-table headers and parse bounded tables; use the model for unfamiliar column mapping, then deterministic code for rows. Link repeated drill IDs to canonical entities within the project context. Prefer complete input evidence over a source search. For the demonstrated table, explicitly respect northing-before-easting ordering.

Add a standard projection engine, such as pyproj/PROJ, rather than handwritten production transformation formulas. Resolve the supported SAD69 / UTM 22S CRS, record the selected transformation, its accuracy and any missing grid/realisation uncertainty. Keep source values and output WGS84 values. Geographic WGS84 assumptions remain labelled; they must not override explicit SAD69 or turn metre values into degrees.

### 2. Resolve a group of entities from one source

Group unresolved assets by project and jurisdiction. Discover a useful report once, persist it, and retrieve its relevant tables and sections for all requested entities. Verify unfamiliar mappings in small batches rather than one model call per hole. Deterministically reuse already verified table structure and CRS context.

Batching does not mean processing every hole in every supporting report. Resolve the entities requested by the input document unless the user explicitly requests source expansion.

### 3. Replace order-dependent limits with resumable scheduling

The four-call search ceiling is our application default (`max_web_requests`), not a provider restriction; it counts discovery API requests, which can contain multiple search-tool actions. A separate twelve-call verification ceiling exists. Cached responses do not consume either limit. Current entity order determines who receives fresh calls.

Keep cost bounds, but expose configured limits before the run, prioritise input evidence and primary assets, share sources, estimate remaining work and preserve resumable per-entity checkpoints. When a limit is reached, report **pending: budget limit**, not **no location found**. Continuing should process pending work without re-extracting or repeating completed calls. Increasing limits alone is not the repair.

### 4. Improve source retrieval and fallback

Index cached reports once with pages, sections, tables and context rather than repeatedly reading all snapshots per entity. Current retrieval is exact name/alias matching with three short windows and one separately found datum snippet; it can omit table headers, zone statements and relevant later sections. Retrieve the complete relevant table plus scoped coordinate-system metadata, with a second targeted retrieval when fields are missing.

Search candidates are proposals. Their URLs, fetch success and evidence must remain separate. If a proposed PDF is unavailable, try another relevant source returned by discovery or a cached copy, with content-hash mirror deduplication. Avoid retrying a permanent 404; retry transient connection/timeouts with backoff. A model's tool-action trace is not proof that it read the full report.

### 5. Simplify extraction and improve identity handling

Keep model-facing role, verification type, names/identifiers, mentions and coordinate evidence. Derive redundant category/subtype routing where possible. Validate physical-place semantics, especially software/websites, laboratory locations, corporate addresses and incidental geographic names.

Use table/section-aware chunk boundaries and bounded overlap, retaining document context. Consolidate exact names/aliases only within compatible project, type and geographic context; preserve ambiguity instead of global name merging. Track row/section coverage and retry only failed or uncovered blocks. The original 17-row table and its 17 identified holes make a concrete coverage check.

Geocoder candidates still need checks. Keep country and meaningful type checks, use broader compatible types and jurisdiction aliases where justified, show ambiguous candidates and their differences, and never use a town's centroid as a project coordinate. Exact coordinate inequality must not automatically imply conflict when two sources report the same location at different precision; compare geometry meaning, source precision and transformation accuracy before accepting or withholding.

### 6. Make state, evidence and versions visible

Separate excluded, pending/unattempted, download failed, identity supported, coordinates awaiting conversion, ambiguous and resolved states. Display original projected values and CRS even before conversion. Distinguish input extraction, source discovery and verification of downloaded passages.

Version extraction, retrieval, transformation and acceptance policies independently. Mark older saved results as older results and offer cached revalidation. Do not require a paid rerun to clear a stale deterministic error. Keep immutable earlier runs and explicit refresh provenance. Record which model checked which passages, source identity, actual query parameters, retries, cost and unknown usage.

The database paper is unsuitable as a representative mining-location example and should be excluded from the evaluation corpus; it can remain a separate negative extraction test.

## Delivery order and acceptance checks

1. **Local coordinate path:** Monte do Carmo's 17 input collar rows attach to 17 entities, convert with recorded SAD69 / UTM 22S context, and retain original row/source evidence. No web search is necessary to obtain those 17 positions. Compare FSA-221 input and report values within their reported precision. Serra Alta deposit remains distinct from its holes and nearby town.
2. **Shared resolution and resumability:** one useful report serves multiple requested holes; a fifth unresolved entity is pending or processed, never misreported as searched-and-missing. Cache replay adds no paid calls. A 404 can fall back to a valid source without losing existing evidence.
3. **Coverage and observability:** annotate a small expected-entity/coordinate set for the representative documents and replay captured real responses offline. Measure recall, precision, coordinate agreement, duplicate rate, skipped work, cost and latency. Add live checks separately so recorded fixtures cannot hide provider changes.

Existing unit/browser tests mainly establish implementation behaviour. The 140 passing tests did not establish real-document coverage: some encoded strict rejection as intended behaviour. Real-document regression fixtures must gate these repairs.

This is Track A repair work. OCR/image-only maps and arbitrary local grids remain explicitly separate extensions. The interview asks for a narrow, explainable POC; aim for a reliable representative slice rather than promising every mining asset can be located automatically.
