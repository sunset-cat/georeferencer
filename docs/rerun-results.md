# Representative-library rerun ? 11 October 2026

The library was rerun with Sol low for extraction/source verification and Luna low for discovery. Results include the completed live jobs and free cached revalidation. Earlier runs remain in SQLite history; the refresh makes no paid calls.

## Observed results

| Document | Resolved | Pending allowance | Other unresolved | Excluded | Live-run estimate |
|---|---:|---:|---:|---:|---:|
| Phoenix / Moss | 6/22 | 8 | 8 | 6 | $0.275 |
| Monte do Carmo | 21/32 | 3 | 8 | 9 | $0.248 |
| Apuradita | 1/4 | 0 | 3 | 7 | $0.104 |
| West McArthur | 4/8 | 0 | 4 | 4 | $0.154 |
| ROB claims | 0/27 | 19 | 8 | 14 | $0.377 |

Recorded live-run total: **$1.159**. Cached deterministic refreshes added **$0**. Prices are estimates of recorded API usage, not an invoice. By stage: extraction $0.420, discovery (tokens plus web actions) $0.406, downloaded-source verification $0.333.

Monte resolves all 17 original input collars, Serra Alta deposit, Brazil, Santa Cruz province and Argentina. Phoenix resolves four input drill holes, Quebec and Canada. West resolves WMA079-01, WMA074-04, Canada and Saskatchewan. Apuradita resolves Bolivia. ROB resolves no eligible entities in this run.

Counts describe accepted records, not measured precision/recall: repeated aliases, context regions and inconsistent role classifications still affect the denominator. Monte and Phoenix improved from 3 and 1 resolved records in their older results; West improved from 2 to 4. Apuradita remains at 1. ROB regressed from 3 to 0, because the first extraction chunk failed and the remaining extraction omitted geographic context. That regression is not considered success.

Key unresolved examples: West McArthur project, Pike Zone, Athabasca Basin and C10S Corridor have supported identities but no accepted geometry. Monte do Carmo project remains distinct from its resolved Serra Alta deposit; East Zone/Pit Norte/Pit Sul and nearby town lack accepted points. Phoenix JV/Moss and several secondary targets remain unsupported or pending. Apuradita sampling coordinates are found but their project-locator meaning is not established (`geometry_scope_unknown`).

Verification: **149 tests passed**; headless Chrome displayed all five documents, location evidence, free refresh/continue controls and the converted FSA-221 coordinates without JavaScript errors. The restarted local server exposes Sol low extraction/verification, Luna search, eight discovery requests, twelve verification requests and a $0.50 soft spending threshold per run.

The machine-readable snapshot is `output/full-rerun-summary.json`, retaining paid run IDs, refresh IDs, usage, costs, resolved names and unresolved states. Previous complete reports and raw responses remain in SQLite run history.

## Implemented and verified

- Install the missing embedded-font decoder (`fontTools`) for subsequent PDF parses; previously cached parsed text is retained.
- Parse header-labelled collar tables in the input, recover omitted drill IDs, retain their row evidence and scoped CRS footnotes.
- Transform complete SAD69/NAD83/WGS84 UTM coordinates with PROJ; retain original values, operation, accuracy and missing-grid metadata. Accept DMS and retain labelled WGS84 assumptions for unspecified geographic datums.
- Share verification in batches of up to six entities from downloaded cached reports; deduplicate mirrors by content. Cache permanent download failures and try other relevant returned sources.
- Separate geocoder, download, discovery and verification allowances; use a soft estimated spending threshold. Pending work is explicit and can be continued without re-extraction or repeating resolved entities.
- Reapply deterministic acceptance to saved evidence without paid tools; display continuation/refresh controls, projected evidence and transformed-coordinate provenance.
- Consolidate matching canonical names within compatible context, compare point precision before declaring conflicts, and isolate bounded provider retries.

## Limits of this delivery

Cached passage retrieval is still bounded name/alias window retrieval, not a complete page/table/section index. Input collar recovery currently supports recognised header/identifier patterns; it is not a universal geological table parser. Completed results are durable, but crash recovery does not yet persist each individual entity while a run is underway. The spending threshold is checked before new discovery/verification calls, can be exceeded by the final call, and cannot account for missing provider usage. Role labels and coordinate semantics remain model judgments requiring review.

## Next proposed fixes from observed failures

1. **Carry scoped geographic context through chunks.** ROB chunk 1/4 failed schema validation because its damaged degree/minute expression could not be parsed. The remainder lost both country and region; this prevents safe geocoder acceptance. Quarantine an invalid coordinate claim rather than discarding all entities/context in its chunk, and retry failed blocks selectively. Build an evidence-backed document/section context object, inherited only where applicable, rather than letting each chunk independently reconstruct it. Kentucky also lacks its US context.
2. **Improve geocoder query construction.** Normalise administrative suffixes and avoid repeating the entity as its own jurisdiction. Tocantins State retrieved a park and hangar rather than the state. Try the canonical administrative name with the country constraint before turning to web search; retain candidate ranking/rejection reasons.
3. **Recover grouped coordinates and OCR damage.** ROB's cover assigns a degree/minute location to the claim group, with degree symbols extracted as `#` and `*`. Recover the group location with explicit OCR provenance and link the individual claims to it as group context, without claiming it is each claim's exact location or boundary.
4. **Define useful on-site geometry semantics.** Apuradita has an explicit WGS84/UTM sample-start point inside its underground workings. Represent that as an on-site sample point with its own scope, optionally usable as a labelled project locator; do not silently promote it to a project centroid. Preserve both conversion status and semantic acceptance status.
5. **Resolve alias duplicates and source-only regions.** Monte still has project/town and Minera Don Nicolas alias duplicates. Add scoped alias consolidation and representative expected-entity fixtures. Athabasca/Pike/West and other zones need a geological spatial record or explicit source coordinates, not the nearby town's point.
6. **Add page/table retrieval and targeted OCR.** Map-only sources and image PDFs are still unsupported. Request OCR only for relevant pages, index table headers and scoped datum statements, and validate bounded fallback sources before additional model calls.

Raising budgets alone will not repair those failures. Continue only after better routing/context has been applied; otherwise continuation repeats expensive identity-only work.
