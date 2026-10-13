import asyncio
import json
import pytest

from georeferencer.config import Settings
from georeferencer.models import Entity, Extraction, VerificationResult
from georeferencer.pipeline import Pipeline
from georeferencer.revalidation import revalidate


@pytest.mark.parametrize('identity_only',[False,True])
def test_cached_refresh_converts_source_quote_and_preserves_pending(monkeypatch,tmp_path,identity_only):
    entities=[Entity(id='hole',name='WMA079-01',category='mining',subtype='drill_hole',
        role='subject_component',aliases=[],identifiers=[],country_code='CA',jurisdiction='Saskatchewan',mentions=[{'quote':'WMA079-01'}]),
        Entity(id='pending',name='Pike Zone',category='mining',subtype='mineralized_zone',
        role='subject_component',aliases=[],identifiers=[],country_code='CA',jurisdiction='Saskatchewan',mentions=[{'quote':'Pike Zone'}])]
    quote='WMA079-01 collared at 477,359 mE / 6,396,539 mN (UTM NAD83 Z13N).'
    claim={'entity_id':'hole','name':'WMA079-01','url':'https://operator.example/report',
        'identity_quote':quote,'coordinate_quote':quote,'crs_quote':'(UTM NAD83 Z13N)',
        'crs':'UTM NAD83 Z13N','longitude':None,'latitude':None,'meaning':'exact_site'}
    class Extractor:
        async def extract(self,*args):return Extraction(entities=entities)
    class OldSearch:
        name='web_search'
        def supports(self,e):return True
        async def verify(self,e,ctx):
            if e.id=='hole':
                ctx.raw_llm_responses.append({'stage':'web_verification','entity_ids':['hole'],
                    'output_text':json.dumps({'candidates':[claim]})})
                return VerificationResult(workflow=self.name,status='supported',identity='supported')
            if identity_only:
                ctx.raw_llm_responses.append({'stage':'web_verification','entity_id':e.id,
                    'output_text':json.dumps({'candidates':[dict(claim,entity_id=e.id,name=e.name,
                        identity_quote='Pike Zone in Saskatchewan.',coordinate_quote=None)]})})
            return VerificationResult(workflow=self.name,status='unavailable',
                issues=[ctx.error('web_budget_exhausted','Pending due to spending limit',self.name,e.id)])
    settings=Settings(log_dir=tmp_path)
    text='WMA079-01 and Pike Zone'
    old=asyncio.run(Pipeline(settings,Extractor(),[OldSearch()]).run('report',text))
    monkeypatch.setattr('georeferencer.revalidation.cached_sources',
        lambda *a,**kw:[{'url':claim['url'],'text':'Pike Zone in Saskatchewan. '+quote}])
    result=asyncio.run(revalidate(settings,{'name':'report','text':text},old))
    hole,pending=result.entities
    assert hole.status=='resolved' and hole.geometry_provenance.crs_status=='transformed'
    assert pending.resolution_state=='pending_budget'
    assert result.usage.model_calls==result.usage.source_calls==0
    assert result.resumed_from==old.request_id
    assert all(r['served_from_cache'] for r in result.raw_llm_responses)
