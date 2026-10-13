Geographic review runs after coordinate resolution. It computes each semantic
unit's observed extent from resolved subaltern locations explicitly linked to it.
General geographic context, excluded locations and the unit's own representative
point do not contribute. The extent is not the project's legal or geological boundary.

Units with at least two located members receive one compact request to the configured
verification model (currently Sol, low reasoning), without web tools or report downloads.
The request contains unit context, the bounding box and its extreme members, capped
at 16 members when extrema tie. Sampling is explicitly disclosed. Interior points
are not audited by this coarse check. Models can miss errors or flag valid points;
this is a plausibility warning, not independent confirmation.

Model findings must reference supplied member IDs. A suspicious member gains
`flagged: true` and `review_flags` explaining why. The unit is also flagged when its
extent is suspicious. Resolution status, coordinates and evidence are retained.
Both fields appear in GeoJSON properties. The UI shows a Flagged for review badge,
reasons and a collapsible Geographic review section with the exact request/response.
Group-level suspicions without identifiable members flag only the unit.

`GEO_GEOGRAPHIC_AUDIT_ENABLED` defaults to true. The per-run request limit defaults
to 8 through `GEO_MAX_GEOGRAPHIC_AUDIT_REQUESTS`; the existing spending threshold
also applies. Skipped, failed and budget-limited reviews are explicitly recorded,
never described as plausible. Audit calls have a separate geographic_audit cost
stage and usage counter. Transport failures do not trigger paid retries.

Responses are cached under data/library/geographic-audit-cache using the prompt,
model, effort and complete audit input as the key. New runs can reuse identical
reviews. Free cached-evidence refresh and the synthetic demo never call this model.
Free refresh preserves existing warnings only when the unit extent and membership,
entity geometry and parent remain unchanged. Existing stored runs are not rewritten
or reviewed just by restarting the server; review is applied during future runs.
