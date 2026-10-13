import asyncio
import json
from types import SimpleNamespace

from georeferencer.config import Settings
from georeferencer.geographic_audit import Audit, model_audit, review_extents
from georeferencer.models import Extraction, VerificationResult
from georeferencer.observability import BudgetExceeded, RunContext
from georeferencer.pipeline import Pipeline
from test_semantic_units import records


def test_flags_preserve_coordinates_and_ignore_context_and_bad_ids(tmp_path):
    rs = records()
    original = [r.geometry for r in rs]
    async def reviewer(settings, request, ctx):
        assert {m['entity_id'] for m in request['members']} == {'aneto', 'ossau'}
        return Audit(status='suspicious', summary='One point is inconsistent.', findings=[
            {'entity_id': 'aneto', 'reason': 'Suspect unit membership.'},
            {'entity_id': 'fr', 'reason': 'Invalid context reference.'}])
    ctx = RunContext('audit', tmp_path, 0, 0)
    extents = asyncio.run(review_extents(rs, Settings(verification_model='test'), ctx, reviewer))
    assert rs[3].flagged and rs[0].flagged
    assert not rs[1].flagged and not rs[4].flagged
    assert [r.geometry for r in rs] == original
    assert rs[3].status == 'resolved'
    assert len(extents[0]['review']['findings']) == 1
    assert ctx.issues[0].code == 'invalid_geographic_audit_id'


def test_budget_and_failure_never_erase_geometry(tmp_path):
    for error, status in [(BudgetExceeded('limit'), 'pending_budget'), (ValueError('malformed'), 'unavailable')]:
        rs = records()
        async def reviewer(*args): raise error
        extents = asyncio.run(review_extents(rs, Settings(verification_model='test'), RunContext(status, tmp_path, 0, 0), reviewer))
        assert extents[0]['review']['status'] == status
        assert rs[3].geometry and not rs[3].flagged


def test_empty_or_single_member_makes_no_request(tmp_path):
    async def reviewer(*args): raise AssertionError('Should not call model')
    result = asyncio.run(review_extents(records()[:4], Settings(verification_model='test'), RunContext('single',tmp_path,0,0), reviewer))
    assert result[0]['review']['status'] == 'insufficient_context'


def test_pipeline_geojson_carries_review_flags(tmp_path):
    rs = records()[:5]
    class Extractor:
        async def extract(self,*args): return Extraction(entities=[r.entity for r in rs])
    class Workflow:
        name='test'
        def supports(self,e): return True
        async def verify(self,e,ctx):
            r=next(r for r in rs if r.entity.id==e.id)
            return VerificationResult(workflow='test',status='supported',identity='supported',geometry=r.geometry,geometry_basis='representative_point')
    async def reviewer(*args):
        return Audit(status='suspicious',summary='Review extent',findings=[{'entity_id':'aneto','reason':'Remote point'}])
    settings=Settings(log_dir=tmp_path,verification_model='test')
    result=asyncio.run(Pipeline(settings,Extractor(),[Workflow()],reviewer).run('test',' '.join(r.entity.name for r in rs),include_context=True))
    feature=next(f for f in result.geojson['features'] if f['id']=='aneto')
    assert feature['properties']['flagged']
    assert feature['properties']['review_flags'][0]['reason']=='Remote point'
    assert feature['geometry']['coordinates']==[0.66,42.63]


def test_one_model_request_no_tools_cached_and_separate_cost_stage(monkeypatch,tmp_path):
    calls=[]
    class Usage:
        input_tokens=100
        output_tokens=50
        total_tokens=150
        input_tokens_details=None
        def model_dump(self): return {'input_tokens':100,'output_tokens':50,'total_tokens':150}
    class Client:
        def __init__(self,**kw): self.responses=self
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
        async def create(self,**kw):
            calls.append(kw)
            assert 'tools' not in kw
            return SimpleNamespace(model='gpt-6.1-sol',status='completed',id='mock',usage=Usage(),output_text=json.dumps({'status':'plausible','summary':'Reasonable','findings':[]}))
    monkeypatch.setattr('georeferencer.geographic_audit.AsyncOpenAI',Client)
    settings=Settings(library_dir=tmp_path,verification_model='gpt-6.1-sol')
    request={'semantic_unit':{'id':'unit'},'bbox':[0,0,1,1]}
    first=RunContext('first',tmp_path,0,0)
    asyncio.run(model_audit(settings,request,first))
    assert first.usage.geographic_audit_requests==1 and first.usage.web_requests==0
    assert first.raw_llm_responses[0]['stage']=='geographic_audit'
    second=RunContext('cached',tmp_path,0,0)
    asyncio.run(model_audit(settings,request,second))
    assert len(calls)==1 and second.usage.model_calls==0
    assert second.raw_llm_responses[0]['served_from_cache']


def test_successful_new_audit_clears_old_warning(tmp_path):
    rs=records()
    rs[3].flagged=True
    rs[3].review_flags=[{'basis':'llm_plausibility_review','semantic_unit':'unit','reason':'Old'}]
    async def reviewer(*args): return Audit(status='plausible',summary='Now reasonable',findings=[])
    asyncio.run(review_extents(rs,Settings(verification_model='test'),RunContext('refresh',tmp_path,0,0),reviewer))
    assert not rs[3].flagged and not rs[3].review_flags
