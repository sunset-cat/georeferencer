from time import perf_counter
import re
from typing import Protocol

from .models import Category, Entity, Point, VerificationResult
from .observability import BudgetExceeded, RunContext, diagnostic
from .sources import Source
from .validation import normalize
from .routing import uses_geocoder


def normalize_jurisdiction(value):
    value = re.sub(r"^(?:state|province|region) of\s+", "", value or "", flags=re.I)
    value = re.sub(r"\s+(?:state|province|region)$", "", value, flags=re.I)
    return normalize(value)


class Workflow(Protocol):
    name: str
    def supports(self, entity: Entity) -> bool: ...
    async def verify(self, entity: Entity, ctx: RunContext) -> VerificationResult: ...


class ExplicitCoordinates:
    name = "explicit_coordinates"

    def supports(self, entity):
        return bool(entity.coordinate_claims)

    async def verify(self, entity, ctx):
        import json
        from .models import Candidate
        from .projections import transform_coordinates
        candidates, issues = [], []
        for claim in entity.coordinate_claims:
            try:
                if claim.meaning == "unknown":
                    raise ValueError("Coordinate spatial meaning is unspecified")
                assumed = claim.crs.strip().upper() in {"UNKNOWN", "UNSPECIFIED", ""}
                evidence = {"coordinate_quote": claim.evidence.quote, "source_crs": claim.crs,
                            "crs": "WGS84", "crs_status": "assumed" if assumed else "stated"}
                if claim.coordinate_format == "projected":
                    if claim.crs_evidence is None or claim.crs_evidence.start is None:
                        raise ValueError("Projected coordinate system needs anchored datum/zone evidence")
                    coords, metadata = transform_coordinates(claim.crs, claim.easting, claim.northing, claim.zone, claim.hemisphere)
                    evidence.update(coordinate_format="projected",original_easting=str(claim.easting),original_northing=str(claim.northing),
                        crs_quote=claim.crs_evidence.quote,crs_status="transformed",transformation=json.dumps(metadata))
                else:
                    explicit_other = re.search(r"SAD\s*69|SIRGAS|NAD\s*(?:27|83)|UTM|EPSG\s*:\s*(?!4326\b)\d+",claim.evidence.quote,re.I)
                    if assumed and explicit_other:
                        raise ValueError("Source explicitly names another coordinate system; WGS84 assumption withheld")
                    if not assumed and claim.crs.upper() not in {"EPSG:4326","WGS84","WGS 84"}:
                        if claim.crs_evidence is None or claim.crs_evidence.start is None:
                            raise ValueError("Coordinate CRS is unsupported without anchored datum evidence")
                        coords, metadata = transform_coordinates(claim.crs,claim.longitude,claim.latitude)
                        evidence.update(crs_status="transformed",transformation=json.dumps(metadata))
                    else:
                        coords = (claim.longitude,claim.latitude)
                candidate = Candidate(name=entity.name,category=entity.category,subtype=entity.subtype,
                    geometry=Point(coordinates=coords),geometry_basis=claim.meaning,source="document",
                    source_record_id=str(claim.evidence.start),independence="same_origin",evidence=evidence)
                candidates.append(candidate)
                ctx.event("coordinates.accepted",entity_id=entity.id,coordinate_format=claim.coordinate_format,
                          geometry=candidate.geometry.model_dump(),evidence=evidence)
            except (ValueError, RuntimeError) as exc:
                issues.append(ctx.error("coordinate_interpretation_unknown", str(exc), self.name, entity.id))
        if len({(c.geometry.coordinates,c.geometry_basis) for c in candidates}) > 1:
            return VerificationResult(workflow=self.name,status="conflicting",candidates=candidates,issues=issues)
        if candidates:
            selected = candidates[0]
            return VerificationResult(workflow=self.name,status="supported",identity="supported",geometry=selected.geometry,
                geometry_basis=selected.geometry_basis,selected_candidate=selected,candidates=candidates,issues=issues)
        return VerificationResult(workflow=self.name,status="unverified",issues=issues)


class SourceWorkflow:
    """Conservative identity matching; no proximity-based acceptance."""
    def __init__(self, name: str, source: Source, categories: set[Category]):
        self.name, self.source, self.categories = name, source, categories

    def supports(self, entity):
        return entity.category in self.categories

    @staticmethod
    def rejection_reasons(entity, candidate):
        """Explain the existing deterministic policy; no LLM ranking."""
        reasons = []
        compatible = candidate.category == entity.category and (
            entity.subtype == 'unknown' or candidate.subtype == entity.subtype or
            {entity.subtype,candidate.subtype} <= {'project','property','mine'})
        if not compatible:
            reasons.append(f"Type differs: requested {entity.category}/{entity.subtype}; returned {candidate.category}/{candidate.subtype}")
        if entity.country_code and normalize(candidate.country_code) != normalize(entity.country_code):
            reasons.append(f"Country differs or is missing: requested {entity.country_code}; returned {candidate.country_code or 'unspecified'}")
        jurisdiction = candidate.jurisdiction
        if not jurisdiction and entity.subtype == "administrative_area" and normalize_jurisdiction(candidate.name) == normalize_jurisdiction(entity.jurisdiction):
            jurisdiction = candidate.name
        if entity.jurisdiction and normalize_jurisdiction(jurisdiction) != normalize_jurisdiction(entity.jurisdiction):
            reasons.append(f"Jurisdiction differs or is missing: requested {entity.jurisdiction}; returned {jurisdiction or 'unspecified'}")
        ids = {(i.namespace, i.value) for i in entity.identifiers}
        id_match = bool(ids.intersection((i.namespace, i.value) for i in candidate.identifiers))
        overlapping = {i.namespace for i in entity.identifiers}.intersection(i.namespace for i in candidate.identifiers)
        if overlapping and not id_match:
            reasons.append("Identifiers conflict within the same namespace")
        names = {normalize(entity.name), *(normalize(n) for n in entity.aliases)}
        name_match = (normalize(candidate.name) in names or entity.subtype=='administrative_area' and
                      normalize_jurisdiction(candidate.name) in {normalize_jurisdiction(n) for n in [entity.name,*entity.aliases]}) and bool(entity.country_code or entity.jurisdiction)
        if not id_match and not name_match:
            reasons.append("No matching identifier or exact name/alias with geographic context")
        return reasons

    async def verify(self, entity, ctx):
        candidates = []
        all_candidates, assessments = [], []
        for attempt in range(2):
            start = perf_counter()
            try:
                ctx.consume("geocoder" if self.name == "geocoder" else "source")
                regional_first = self.name == "geocoder" and hasattr(self.source, "query_text")
                refined = (bool(entity.jurisdiction) and attempt == 0) if regional_first else bool(attempt)
                query = self.source.query_text(entity, refined=refined) if hasattr(self.source, "query_text") else entity.name
                if regional_first:
                    reason = ("Initial name query with region and country context" if refined else
                              "No candidate passed the regional query; retry name with country only" if attempt else
                              "Initial name query; no region available")
                else:
                    reason = "No candidate passed the first query; append jurisdiction once" if attempt else "Initial name query"
                reason = getattr(self.source, 'query_reason', reason)
                ctx.event("source.request", workflow=self.name, source=self.source.name, entity_id=entity.id,
                          query=query, country_code=entity.country_code, jurisdiction=entity.jurisdiction,
                          refined=refined, query_strategy="region_then_country" if regional_first else "name_then_region",
                          query_reason=reason,
                          attempt=attempt + 1)
                candidates = await self.source.search(entity, refined=refined)
                current_assessments = []
                matches = []
                for candidate in candidates:
                    reasons = self.rejection_reasons(entity, candidate)
                    current_assessments.append({"source": candidate.source, "source_record_id": candidate.source_record_id,
                                                "attempt": attempt + 1, "passes_filters": not reasons, "reasons": reasons})
                    if not reasons:
                        matches.append(candidate)
                all_candidates.extend(candidates)
                assessments.extend(current_assessments)
                ctx.event("source.response", workflow=self.name, entity_id=entity.id,
                          latency_ms=round((perf_counter() - start) * 1000), candidate_count=len(candidates),
                          cache_hit=getattr(self.source, "last_cache_hit", False),
                          candidates=[c.model_dump(mode="json") for c in candidates],
                          candidate_assessments=current_assessments)
            except Exception as exc:
                code = "budget_exhausted" if isinstance(exc, BudgetExceeded) else "source_error"
                issue = ctx.error(code, f"{self.source.name}: {diagnostic(exc)}", self.name, entity.id, retryable=code == "source_error")
                ctx.event("source.failure", entity_id=entity.id, error_type=type(exc).__name__, latency_ms=round((perf_counter() - start) * 1000), http_status=getattr(getattr(exc, "response", None), "status_code", None))
                return VerificationResult(workflow=self.name, status="unavailable", issues=[issue],
                                          candidates=all_candidates, candidate_assessments=assessments)
            # Repeated rows from one record are not independent corroboration.
            distinct = {(c.source, c.source_record_id): c for c in matches}
            signatures = {(c.source, c.source_record_id, c.geometry.model_dump_json() if c.geometry else None, c.geometry_basis) for c in matches}
            if len(signatures) > len(distinct):
                issue = ctx.error("inconsistent_source_record", "The same source record supplies conflicting geometry", self.name, entity.id)
                return VerificationResult(workflow=self.name, status="conflicting", candidates=all_candidates, issues=[issue], candidate_assessments=assessments)
            if len(distinct) == 1:
                selected = next(iter(distinct.values()))
                geometry = selected.geometry if selected.geometry_basis != "unknown" else None
                return VerificationResult(workflow=self.name, status="confirmed", identity="confirmed",
                    geometry=geometry, geometry_basis=selected.geometry_basis if geometry else None,
                    candidates=all_candidates, selected_candidate=selected, candidate_assessments=assessments,
                    independently_confirmed=bool(geometry and selected.independence == "independent"))
            if len(distinct) > 1:
                issue = ctx.error("ambiguous_identity", "Multiple source records match; no automatic selection", self.name, entity.id)
                return VerificationResult(workflow=self.name, status="conflicting", candidates=all_candidates, issues=[issue], candidate_assessments=assessments)
            # Without jurisdiction, regional and country-only queries are identical.
            if not entity.jurisdiction or not getattr(self.source, "supports_refinement", False):
                break
        return VerificationResult(workflow=self.name, status="no_match", candidates=all_candidates, candidate_assessments=assessments)


class GeocoderWorkflow(SourceWorkflow):
    def __init__(self, name, source, categories, settings=None, query_proposer=None):
        super().__init__(name, source, categories)
        self.settings, self.query_proposer = settings, query_proposer

    def supports(self, entity):
        return super().supports(entity) and uses_geocoder(entity)

    async def query(self, entity, query, reason, ctx):
        source = self.source
        class QuerySource:
            name = source.name
            supports_refinement = False
            query_reason = reason
            def query_text(self, *args, **kwargs): return query
            @property
            def last_cache_hit(self): return source.last_cache_hit
            async def search(self, original, refined=False):
                return await source.search(original.model_copy(update={'name':query}), refined=False)
        # Search text changes, but validation always uses the ORIGINAL entity.
        return await SourceWorkflow(self.name, QuerySource(), self.categories).verify(entity, ctx)

    @staticmethod
    def merge_results(entity, previous, current, ctx):
        candidates = previous.candidates + current.candidates
        assessments = previous.candidate_assessments + current.candidate_assessments
        matches = [c for c in candidates if not SourceWorkflow.rejection_reasons(entity,c)]
        distinct = {(c.source,c.source_record_id):c for c in matches}
        signatures = {(c.source,c.source_record_id,c.geometry.model_dump_json() if c.geometry else None,c.geometry_basis) for c in matches}
        issues = previous.issues + current.issues
        if len(distinct)>1 or len(signatures)>len(distinct):
            return VerificationResult(workflow='geocoder',status='conflicting',candidates=candidates,
                candidate_assessments=assessments,issues=issues)
        if len(distinct)==1:
            selected=next(iter(distinct.values()))
            return VerificationResult(workflow='geocoder',status='confirmed',identity='confirmed',
                geometry=selected.geometry if selected.geometry_basis!='unknown' else None,
                geometry_basis=selected.geometry_basis,selected_candidate=selected,
                independently_confirmed=selected.independence=='independent',
                candidates=candidates,candidate_assessments=assessments,issues=issues)
        return VerificationResult(workflow='geocoder',status=current.status if current.status=='unavailable' else 'no_match',
            candidates=candidates,candidate_assessments=assessments,issues=issues)

    async def deterministic(self, entity, ctx):
        result=await super().verify(entity,ctx)
        if result.status not in {'no_match','conflicting'} or not hasattr(self.source,'query_text'):
            return result
        # Only administrative suffix removal and aliases already in extraction.
        names=list(entity.aliases)
        if entity.subtype=='administrative_area':
            names.insert(0,re.sub(r'\s+(?:state|province|region)$','',
                re.sub(r'^(?:state|province|region) of\s+','',entity.name,flags=re.I),flags=re.I).strip())
        seen={normalize(self.source.query_text(entity,False)),normalize(self.source.query_text(entity,True))}
        count=0
        for query in names:
            if not query.strip() or normalize(query) in seen:continue
            seen.add(normalize(query));count+=1
            current=await self.query(entity,query,'Document alias or administrative-name cleanup',ctx)
            result=self.merge_results(entity,result,current,ctx)
            if result.geometry is not None or current.status=='unavailable' or count==3:break
        return result

    async def prepare(self, entities, ctx):
        results=getattr(ctx,'geocoder_results',None)
        if results is None:
            results=ctx.geocoder_results={}
        eligible=[e for e in entities if self.supports(e)]
        for entity in eligible:
            results[entity.id]=await self.deterministic(entity,ctx)
        if not self.settings or not self.settings.geocoder_refinement_enabled or not (self.settings.web_search_model or self.settings.model):return
        failed=[e for e in eligible if results[e.id].status in {'no_match','conflicting'}]
        groups={}
        for entity in failed:
            group=entity.semantic_unit or (entity.id if entity.entity_kind=='semantic_unit' else 'document_geographic_context')
            groups.setdefault(group,[]).append(entity)
        from .geocoder_refinement import propose_queries,refinement_request
        for group,members in groups.items():
            request=refinement_request(group,members,results,ctx)
            try:
                suggestions=await (self.query_proposer or propose_queries)(self.settings,request,ctx)
                allowed={e.id:e for e in members}
                seen={e['entity_id']:{normalize(q) for q in e['previous_queries']} for e in request['entities']}
                counts={e.id:0 for e in members}
                for proposal in suggestions.queries:
                    if proposal.entity_id not in allowed:
                        ctx.error('invalid_geocoder_refinement_id','Query suggestion referenced an unknown entity','geocoder_refinement');continue
                    entity=allowed[proposal.entity_id];query=proposal.query.strip()
                    if normalize(query) in seen[entity.id] or counts[entity.id]>=3 or results[entity.id].geometry is not None:continue
                    seen[entity.id].add(normalize(query));counts[entity.id]+=1
                    current=await self.query(entity,query,'LLM query refinement: '+proposal.reason,ctx)
                    results[entity.id]=self.merge_results(entity,results[entity.id],current,ctx)
                    if current.status=='unavailable':counts[entity.id]=3
            except Exception as exc:
                code='geocoder_refinement_budget_exhausted' if isinstance(exc,BudgetExceeded) else 'geocoder_refinement_failed'
                for entity in members:
                    results[entity.id].issues.append(ctx.error(code,diagnostic(exc),'geocoder_refinement',entity.id))

    async def verify(self, entity, ctx):
        if entity.id in getattr(ctx,'geocoder_results',{}):return ctx.geocoder_results[entity.id]
        return await self.deterministic(entity,ctx)
