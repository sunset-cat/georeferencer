import hashlib
import json
from time import perf_counter
from uuid import uuid4

from .config import Settings
from .extraction import Extractor
from .ingestion import ingest
from .identity import consolidate
from .models import Category, Resolution, RunResult, GeometryProvenance
from .observability import RunContext, diagnostic
from .pricing import estimate_cost
from .coordinates import coordinate_evidence_matches
from .sources import LocalRegistry, NominatimSource, PhotonSource
from .validation import evidence_errors
from .workflows import ExplicitCoordinates, SourceWorkflow, GeocoderWorkflow, Workflow
from .routing import is_mining_asset, uses_geocoder

ROUTING_VERSION = "tables-proj-shared-resume-v1"


def web_search_decision(entity, results):
    if any(r.workflow == "explicit_coordinates" and r.geometry is not None and r.status in {"supported", "confirmed"} for r in results):
        return False, "Input document already supplies accepted coordinates; external discovery unnecessary"
    if is_mining_asset(entity):
        return True, "Mining asset: use web verification rather than a locality geocoder"
    if not uses_geocoder(entity):
        return True, "Entity is not classified as a geographic place; use web verification"
    geocoder_matches = [result for result in results if result.workflow == "geocoder"]
    if any(result.status in {"supported", "confirmed"} and result.identity in {"supported", "confirmed"}
           and result.geometry is not None and result.geometry_basis in {"exact_site", "representative_point"}
           for result in geocoder_matches):
        return False, "Geocoder supplied an accepted match for a non-mining location"
    return True, "Geocoder supplied no acceptable match or was unavailable"


class Pipeline:
    def __init__(self, settings: Settings, extractor: Extractor, workflows: list[Workflow] | None = None, geographic_reviewer=None):
        self.settings, self.extractor = settings, extractor
        self.geographic_reviewer = geographic_reviewer
        self.workflows = workflows if workflows is not None else default_workflows(settings)

    async def run(self, name: str, content: str, format: str = "text", include_context: bool = False, request_id: str | None = None, resume: RunResult | None = None):
        ctx = RunContext(request_id or uuid4().hex, self.settings.log_dir,
                         self.settings.max_source_calls, self.settings.max_model_calls)
        ctx.event("request.started", document_name=name, format=format, characters=len(content),
                  document_sha256=hashlib.sha256(content.encode()).hexdigest(), include_context=include_context,
                  routing_version=ROUTING_VERSION)
        resolutions = []
        ctx.max_geocoder_calls = self.settings.max_geocoder_calls
        ctx.max_download_calls = self.settings.max_download_calls
        relationships = []
        try:
            document = ingest(name, content, format)
            if len(document.text) > self.settings.max_document_characters:
                raise ValueError(f"document exceeds {self.settings.max_document_characters} characters")
            feedback = []
            extraction = None
            for attempt in range(2):
                try:
                    if resume and resume.workflow_version == ROUTING_VERSION:
                        from .models import Extraction
                        extraction = Extraction(entities=[r.entity.model_copy(deep=True) for r in resume.entities])
                        ctx.event('extraction.resumed',previous_request_id=resume.request_id,entity_count=len(extraction.entities))
                    else:
                        extraction = await self.extractor.extract(document, ctx, feedback)
                except ValueError as exc:
                    feedback = [diagnostic(exc)]
                    ctx.event("extraction.validation_failed", kind="schema", attempt=attempt + 1, errors=feedback)
                    if attempt == 0:
                        continue
                    if extraction is None:
                        raise
                    break
                from .coordinate_tables import attach_tables
                attach_tables(extraction, document, ctx)
                feedback = evidence_errors(extraction, document)
                if not feedback:
                    break
                ctx.previous_extraction = extraction.model_dump(mode="json")
                ctx.event("extraction.validation_failed", kind="evidence", attempt=attempt + 1, errors=feedback)
            # Quarantine unsupported evidence individually after bounded correction.
            final_evidence_errors = evidence_errors(extraction, document)
            invalid_entities = set()
            for entity in extraction.entities:
                valid_mentions = [m for m in entity.mentions if m.start is not None]
                evidence_detail = "; ".join(error for error in final_evidence_errors if error.startswith(entity.id + ":"))
                if not valid_mentions:
                    invalid_entities.add(entity.id)
                    ctx.error("invalid_extraction_evidence", "No mention could be anchored; geocoding and web verification skipped. " + evidence_detail, "extraction", entity.id)
                else:
                    if len(valid_mentions) != len(entity.mentions):
                        ctx.error("invalid_extraction_evidence", "Unsupported mentions removed. " + evidence_detail, "extraction", entity.id)
                    entity.mentions = valid_mentions
                valid_claims = []
                for claim in entity.coordinate_claims:
                    if claim.evidence.start is not None and coordinate_evidence_matches(claim):
                        valid_claims.append(claim)
                    else:
                        ctx.error("invalid_coordinate_evidence", "Unsupported coordinate claim removed", "extraction", entity.id)
                entity.coordinate_claims = valid_claims
            extraction.relationships = [r for r in extraction.relationships if r.evidence.start is not None and r.subject not in invalid_entities and r.object not in invalid_entities]
            if extraction.entities and len(invalid_entities) == len(extraction.entities):
                raise ValueError("No extracted entity has valid document evidence")
            extraction, merged = consolidate(extraction)
            for original, target in merged:
                ctx.event("entity.merged", original_entity_id=original, entity_id=target, reason="identical_namespaced_identifiers_and_context")
            relationships = extraction.relationships
            previous = {r.entity.id:r for r in resume.entities} if resume and resume.workflow_version==ROUTING_VERSION else {}
            eligible = [e for e in extraction.entities if e.id not in invalid_entities and
                (e.role in {'primary_subject','subject_component'} or include_context and e.role=='context_location') and
                not (e.id in previous and previous[e.id].status in {'resolved','excluded'})]
            ctx.pending_entities = [e for e in eligible if is_mining_asset(e) or not uses_geocoder(e)]
            # Input coordinates take priority, then primary assets, then components,
            # finally context; order of mention must not allocate the whole budget.
            priority={'primary_subject':0,'subject_component':1,'context_location':2}
            output_order = {e.id:index for index,e in enumerate(extraction.entities)}
            extraction.entities.sort(key=lambda e:(not bool(e.coordinate_claims),priority.get(e.role,3)))
            for workflow in self.workflows:
                if hasattr(workflow,'plan_discovery'):
                    workflow.plan_discovery(extraction.entities,eligible,ctx)
            for workflow in self.workflows:
                if hasattr(workflow,'prepare'):
                    await workflow.prepare([e for e in eligible if uses_geocoder(e)] if workflow.name=='geocoder' else
                        [e for e in eligible if is_mining_asset(e) or not uses_geocoder(e)],ctx)
            ctx.event("extraction.completed", entity_count=len(extraction.entities), relationship_count=len(relationships))
            for entity in extraction.entities:
                if entity.id in previous and previous[entity.id].status in {'resolved','excluded'}:
                    ctx.completed_entity_ids.add(entity.id)
                    resolutions.append(previous[entity.id].model_copy(deep=True))
                    ctx.event('entity.checkpoint_reused',entity_id=entity.id,previous_request_id=resume.request_id)
                    continue
                if entity.id in invalid_entities:
                    resolutions.append(Resolution(entity=entity, status="unresolved", reason="Document evidence could not be validated", issues=[i for i in ctx.issues if i.entity_id == entity.id]))
                    continue
                if entity.role not in {"primary_subject", "subject_component"} and not (include_context and entity.role == "context_location"):
                    resolutions.append(Resolution(entity=entity, status="excluded", reason=f"Verification skipped by role policy: {entity.role}"))
                    ctx.event("entity.excluded", entity_id=entity.id, reason=entity.role)
                    continue
                results = []
                for workflow in self.workflows:
                    if not workflow.supports(entity):
                        continue
                    if workflow.name == "web_search":
                        should_search, reason = web_search_decision(entity, results)
                        ctx.event("web.routing", entity_id=entity.id, search=should_search, reason=reason)
                        if not should_search:
                            continue
                    elif getattr(workflow, "fallback", False) and any(
                        r.status == "conflicting" or (r.geometry is not None and
                            r.status in {"supported", "confirmed"} and
                            r.identity in {"supported", "confirmed"} and
                            r.geometry_basis in {"exact_site", "representative_point"})
                        for r in results
                    ):
                        continue
                    try:
                        result = await workflow.verify(entity, ctx)
                        results.append(result)
                        ctx.event("verification.completed", entity_id=entity.id, **result.model_dump(mode="json"))
                    except Exception as exc:
                        # A broken plugin must not erase other entities or evidence.
                        issue = ctx.error("workflow_error", f"{workflow.name}: {diagnostic(exc)}", workflow.name, entity.id)
                        from .models import VerificationResult
                        results.append(VerificationResult(workflow=workflow.name, status="unavailable", issues=[issue]))
                geometry_results = [r for r in results if r.geometry is not None
                                    and r.status in {"supported", "confirmed"}
                                    and r.identity in {"supported", "confirmed"}
                                    and r.geometry_basis in {"exact_site", "representative_point"}]
                from .geometry_checks import compatible_points
                from types import SimpleNamespace
                comparable=[r.selected_candidate or SimpleNamespace(geometry=r.geometry,geometry_basis=r.geometry_basis,evidence={}) for r in geometry_results]
                conflict = any(not compatible_points(a,b) for index,a in enumerate(comparable) for b in comparable[index+1:]) or any(r.status == "conflicting" for r in results)
                if conflict:
                    ctx.error("conflicting_location", "Identity or geometry evidence conflicts; geometry withheld", "acceptance", entity.id)
                selected = geometry_results[0] if geometry_results and not conflict else None
                provenance = None
                if selected:
                    candidate = selected.selected_candidate
                    if selected.workflow == "explicit_coordinates":
                        claim = next(c for c in entity.coordinate_claims if str(c.evidence.start) == candidate.source_record_id)
                        provenance = GeometryProvenance(origin="document", workflow=selected.workflow, source=name, evidence=claim.evidence,
                            crs="WGS84", source_crs=claim.crs, crs_status=candidate.evidence.get("crs_status"),
                            transformation=json.loads(candidate.evidence.get("transformation", "{}")))
                    elif candidate:
                        provenance = GeometryProvenance(origin="external", workflow=selected.workflow,
                            source=candidate.source, source_record_id=candidate.source_record_id,
                            source_record_url=candidate.source_record_url, crs=candidate.evidence.get("crs"),
                            source_crs=candidate.evidence.get("source_crs"), crs_status=candidate.evidence.get("crs_status"),
                            transformation=json.loads(candidate.evidence.get("transformation", "{}")))
                resolution = Resolution(entity=entity,
                    reason=None if selected else "Conflicting evidence; geometry withheld" if conflict else "No verification source configured for this type" if not results else "Entity identity supported by a source, but no acceptable coordinates were established" if any(r.identity in {"supported", "confirmed"} for r in results) else "No source match supplied acceptable geometry",
                    status="conflicting" if conflict else "resolved" if selected else "unresolved",
                    geometry=selected.geometry if selected else None,
                    geometry_basis=selected.geometry_basis if selected else None,
                    geometry_provenance=provenance,
                    independently_confirmed=bool(selected and any(r.independently_confirmed for r in geometry_results)),
                    verification=results, issues=[i for i in ctx.issues if i.entity_id == entity.id])
                resolutions.append(resolution)
                ctx.pending_entities = [e for e in ctx.pending_entities if e.id!=entity.id]
                codes = {i.code for i in resolution.issues}
                resolution.resolution_state = 'resolved' if selected else 'ambiguous' if conflict else 'pending_budget' if any('budget' in c for c in codes) else 'evidence_invalid' if 'invalid_extraction_evidence' in codes else 'geometry_scope_unknown' if any('spatial meaning' in c.evidence.get('coordinate_validation','') for v in results for c in v.candidates) else 'awaiting_conversion' if 'coordinate_interpretation_unknown' in codes or any(c.evidence.get('coordinate_format')=='projected' for v in results for c in v.candidates) else 'source_unavailable' if any(c in codes for c in ['web_source_download_failed','web_source_parse_failed','web_project_discovery_failed','web_verification_failed','workflow_error','source_error']) else 'identity_only' if any(v.identity in {'supported','confirmed'} for v in results) else 'no_match'
                if selected:ctx.completed_entity_ids.add(entity.id)
                if resolution.resolution_state=='pending_budget':
                    resolution.reason='Pending: configured request or spending limit reached; continue this run to process unfinished work'
                ctx.event("entity.resolved", entity_id=entity.id, status=resolution.status)
            resolutions.sort(key=lambda r:output_order[r.entity.id])
            status = "partial" if ctx.issues or any(r.status in {"unresolved", "conflicting"} for r in resolutions) else "completed"
        except Exception as exc:
            ctx.error("pipeline_error", f"{type(exc).__name__}: {diagnostic(exc)}", "pipeline")
            ctx.event("pipeline.failure", error_type=type(exc).__name__,
                      provider_request_id=getattr(exc, "request_id", None), http_status=getattr(exc, "status_code", None))
            status = "failed"
        from .geographic_audit import review_extents
        extents = await review_extents(resolutions, self.settings, ctx, self.geographic_reviewer) if status != 'failed' else []
        if ctx.issues and status == 'completed':
            status = 'partial'
        features = []
        for r in resolutions:
            if r.geometry is not None:
                features.append({"type": "Feature", "id": r.entity.id, "geometry": r.geometry.model_dump(mode="json"),
                    "properties": {"name": r.entity.name, "category": r.entity.category.value,
                        "subtype": r.entity.subtype, "role": r.entity.role,
                        "entity_kind":r.entity.entity_kind,"semantic_unit":r.entity.semantic_unit,
                        "flagged":r.flagged,"review_flags":r.review_flags,
                        "verification_classes": [v.value for v in r.entity.verification_classes],
                        "mentions": [m.model_dump() for m in r.entity.mentions],
                        "geometry_basis": r.geometry_basis, "independently_confirmed": r.independently_confirmed,
                        "geometry_provenance": r.geometry_provenance.model_dump(mode="json") if r.geometry_provenance else None,
                        "verification": [v.model_dump(mode="json") for v in r.verification]}})
        result = RunResult(request_id=ctx.request_id, status=status,
                           geojson={"type": "FeatureCollection", "features": features},
                           entities=resolutions, relationships=relationships, issues=ctx.issues, usage=ctx.usage,
                           raw_llm_responses=ctx.raw_llm_responses,
                           estimated_cost=estimate_cost(ctx.usage, ctx.raw_llm_responses), workflow_version=ROUTING_VERSION,
                           limits={"web_discovery_requests":self.settings.max_web_requests,"source_verification_requests":self.settings.max_verification_requests,
                                   "geocoder_refinement_requests":self.settings.max_geocoder_refinement_requests,
                                   "estimated_cost_usd":self.settings.max_run_cost_usd,"geocoder_calls":self.settings.max_geocoder_calls,"downloads":self.settings.max_download_calls},
                           resumed_from=resume.request_id if resume else None,
                           progress={"resolved":sum(r.status=='resolved' for r in resolutions),"excluded":sum(r.status=='excluded' for r in resolutions),
                                     "pending":sum(r.resolution_state=='pending_budget' for r in resolutions),"unresolved":sum(r.status=='unresolved' and r.resolution_state!='pending_budget' for r in resolutions)})
        result.semantic_unit_extents=extents
        ctx.event("request.completed", status=status, feature_count=len(features), issue_count=len(ctx.issues),
                  usage=ctx.usage.model_dump(), estimated_cost=result.estimated_cost.model_dump(),
                  latency_ms=round((perf_counter() - ctx.started) * 1000))
        return result


def default_workflows(settings):
    workflows: list[Workflow] = []
    if settings.geocoder_url:
        providers = {"nominatim": NominatimSource, "photon": PhotonSource}
        if settings.geocoder_provider not in providers:
            raise ValueError("GEO_GEOCODER_PROVIDER must be photon or nominatim")
        source = providers[settings.geocoder_provider](settings.geocoder_url,
            settings.geocoder_user_agent, settings.timeout_seconds, cache_path=settings.library_dir / "geocoder-cache.sqlite3")
        workflows.append(GeocoderWorkflow("geocoder", source, set(Category), settings=settings))
    workflows.append(ExplicitCoordinates())
    if settings.registry_path:
        source = LocalRegistry(settings.registry_path)
        workflows += [SourceWorkflow("geographic_registry", source, {Category.geographic}),
                      SourceWorkflow("geological_survey", source, {Category.geological}),
                      SourceWorkflow("mining_registry", source, {Category.mining})]
    if settings.web_search_enabled and settings.model:
        from .web_resolution import WebResolution
        workflows.append(WebResolution(settings))
    return workflows
