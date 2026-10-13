"""Reapply deterministic acceptance to saved input/results without paid tools."""
import json

from .models import Extraction, VerificationResult
from .web_resolution import WebResolution, validate_candidate
from .source_passages import cached_sources
from .workflows import SourceWorkflow, ExplicitCoordinates
from .pipeline import Pipeline


class SavedExtraction:
    def __init__(self,previous): self.previous=previous
    async def extract(self,document,ctx,feedback):
        if not ctx.raw_llm_responses:
            ctx.raw_llm_responses.extend({**r,'served_from_cache':True} for r in self.previous.raw_llm_responses)
        return Extraction(entities=[r.entity.model_copy(deep=True) for r in self.previous.entities])


class SavedSource:
    def __init__(self,name,previous):self.name=name; self.previous={r.entity.id:r for r in previous.entities}
    def supports(self,entity):return entity.id in self.previous and any(v.workflow==self.name for v in self.previous[entity.id].verification)
    async def verify(self,entity,ctx):
        old=next(v for v in self.previous[entity.id].verification if v.workflow==self.name)
        candidates=[c for c in old.candidates if not SourceWorkflow.rejection_reasons(entity,c)]
        unique={(c.source,c.source_record_id):c for c in candidates}
        if len(unique)>1:return VerificationResult(workflow=self.name,status='conflicting',candidates=old.candidates)
        if unique:
            selected=next(iter(unique.values()))
            return VerificationResult(workflow=self.name,status='supported',identity='supported',candidates=old.candidates,
                selected_candidate=selected,geometry=selected.geometry if selected.geometry_basis!='unknown' else None,
                geometry_basis=selected.geometry_basis,independently_confirmed=old.independently_confirmed)
        return VerificationResult(workflow=self.name,status='no_match',candidates=old.candidates,candidate_assessments=old.candidate_assessments)


class SavedWeb:
    name='web_search'
    def __init__(self,settings,previous):self.settings=settings;self.previous=previous
    def supports(self,entity):return True
    async def verify(self,entity,ctx):
        texts={s['url']:s['text'] for s in cached_sources(self.settings,entity,limit=None)}
        claims=[]
        verifier_claims=[]
        for raw in self.previous.raw_llm_responses:
            if raw.get('stage') not in {'web_search','web_verification'}:continue
            if raw.get('entity_ids'):
                if entity.id not in raw['entity_ids']:continue
            elif raw.get('entity_id')!=entity.id:continue
            try:
                for claim in json.loads(raw['output_text']).get('candidates',[]):
                    if raw.get('entity_ids') and claim.get('entity_id')!=entity.id:continue
                    if claim.get('url') in texts:
                        (verifier_claims if raw.get('stage')=='web_verification' else claims).append(claim)
            except (ValueError,KeyError):continue
        claims=verifier_claims or claims
        old=next((v for r in self.previous.entities if r.entity.id==entity.id for v in r.verification if v.workflow==self.name),None)
        if not claims and old:
            # Keep a pending/not-attempted result distinguishable from a search
            # that actually found no match. No network retry happens here.
            restored=old.model_copy(deep=True)
            ctx.issues.extend(restored.issues)
            return restored
        candidates,issues=[],[]
        for claim in claims:
            try:candidates.append(validate_candidate(entity,claim,texts[claim['url']]))
            except ValueError as exc:issues.append(ctx.error('web_evidence_rejected',str(exc),self.name,entity.id))
        result=WebResolution(self.settings).result(candidates,issues,[{'url':url,'title':'Cached source'} for url in texts])
        if old and result.geometry is None:
            pending=[i.model_copy(deep=True) for i in old.issues if 'budget' in i.code]
            result.issues.extend(pending)
            ctx.issues.extend(pending)
        return result


async def revalidate(settings,row,previous):
    from dataclasses import replace
    names={v.workflow for r in previous.entities for v in r.verification}-{'web_search','explicit_coordinates'}
    workflows=[ExplicitCoordinates(),*(SavedSource(name,previous) for name in sorted(names)),SavedWeb(settings,previous)]
    result=await Pipeline(replace(settings, geographic_audit_enabled=False),SavedExtraction(previous),workflows).run(row['name'],row['text'],include_context=True)
    old_extents={e['id']:e for e in previous.semantic_unit_extents}
    unchanged_units=set()
    for extent in result.semantic_unit_extents:
        old=old_extents.get(extent['id'])
        if old and old.get('bbox')==extent['bbox'] and old.get('located_member_ids')==extent['located_member_ids']:
            extent['review']=old.get('review',extent['review'])
            unchanged_units.add(extent['id'])
    old_entities={r.entity.id:r for r in previous.entities}
    for record in result.entities:
        old=old_entities.get(record.entity.id)
        if old and old.geometry==record.geometry and old.entity.semantic_unit==record.entity.semantic_unit:
            record.review_flags=[f for f in old.review_flags if f.get('semantic_unit') in unchanged_units]
            record.flagged=bool(record.review_flags)
    for feature in result.geojson['features']:
        record=next(r for r in result.entities if r.entity.id==feature['id'])
        feature['properties'].update(flagged=record.flagged,review_flags=record.review_flags)
    result.resumed_from=previous.request_id
    return result
