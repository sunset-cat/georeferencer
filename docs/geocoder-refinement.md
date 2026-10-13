Geocoder retries retain the original entity identity and geography constraints.
The first search uses region and country when available; the next drops the region.
If no candidate passes, up to three document aliases or cleaned administrative names
are tried. Only administrative-area entities have State/Province/Region descriptors
removed, without modifying their saved names. Every query and its response is logged.

During a full pipeline run, remaining no-match and ambiguous results are batched by
their explicit semantic unit. Unlinked geographic context entities share one document
context batch. The configured discovery model (Luna, low reasoning) proposes query
strings and reasons, not coordinates or candidate records. Python keeps country,
jurisdiction, type and identity filters unchanged. Queries already tried are skipped,
unknown entity IDs rejected, and at most three new queries run per entity. Any
multiple qualifying records remain ambiguous; narrower queries cannot silently
erase previously found matching records. There is no recursive refinement loop.

The model call has no web tools. Responses are cached in
data/library/geocoder-refinement-cache, keyed by model, effort, prompt and input.
The request contains document mention context, prior queries, bounded returned
records and rejection reasons. It is counted separately as geocoder_refinement in
usage, logs and UI cost estimates. GEO_GEOCODER_REFINEMENT_ENABLED defaults to true;
GEO_MAX_GEOCODER_REFINEMENT_REQUESTS defaults to 8 per run. The run spending threshold
and geocoder request limit apply. Failure is logged and existing results retained.
Free cached-evidence refresh and the synthetic demo do not invoke the model.

On 12 October 2026, a targeted live test resolved Tocantins State without a model
call through the cleaned query Tocantins. A separate diagnostic deliberately
bypassed successful cleanup and gave Luna the original failed query; it suggested
Tocantins, Brazil, which returned the accepted state record relation:336819.
One Luna request cost approximately $0.0001342. The library was refreshed from the
new geocoder evidence without repeating extraction, web discovery or geographic
audit. Its current counts are 22 resolved, 8 unresolved, 6 excluded, zero pending.
