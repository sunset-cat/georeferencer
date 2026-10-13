import asyncio
import json

from georeferencer.config import Settings
from georeferencer.geocoder_refinement import Suggestions
from georeferencer.models import Candidate,Category,Entity,Point
from georeferencer.observability import RunContext
from georeferencer.workflows import GeocoderWorkflow


def location(identifier='state',name='Tocantins State',parent=None):
    return Entity(id=identifier,name=name,category='general_geographic',subtype='administrative_area',
        role='context_location',aliases=[],mentions=[{'quote':name}],identifiers=[],
        country_code='BR',jurisdiction=None,entity_kind='subaltern_location' if parent else 'general_geographic',semantic_unit=parent)


def candidate(name='Tocantins',identifier='state',country='BR'):
    return Candidate(name=name,category='general_geographic',subtype='administrative_area',country_code=country,
        geometry=Point(coordinates=(-48.37,-10.88)),geometry_basis='representative_point',source='fixture',source_record_id=identifier)


class Source:
    name='photon'
    last_cache_hit=False
    supports_refinement=True
    def __init__(self,responses):self.responses=responses;self.calls=[]
    def query_text(self,e,refined=False):return e.name
    async def search(self,e,refined=False):
        self.calls.append((e.name,e.country_code))
        return self.responses.get(e.name,[])


def test_cleanup_resolves_without_llm_and_preserves_name(tmp_path):
    source=Source({'Tocantins':[candidate()]})
    async def forbidden(*args):raise AssertionError('No LLM needed')
    wf=GeocoderWorkflow('geocoder',source,set(Category),Settings(web_search_model='test'),forbidden)
    ctx=RunContext('clean',tmp_path,20,0);e=location()
    asyncio.run(wf.prepare([e],ctx));r=asyncio.run(wf.verify(e,ctx))
    assert r.geometry and r.status=='confirmed'
    assert source.calls==[('Tocantins State','BR'),('Tocantins','BR')]
    assert e.name=='Tocantins State'
    assert ctx.usage.model_calls==0


def test_batch_queries_deduplicate_cap_and_validate_original_identity(tmp_path):
    es=[location('a','Alpha','unit'),location('b','Beta','unit')]
    source=Source({'Alpha query':[candidate('Alpha','a')], 'Beta wrong-country':[candidate('Beta','b',country='US')]})
    calls=[]
    async def proposer(settings,request,ctx):
        calls.append(request)
        return Suggestions(queries=[{'entity_id':'a','query':'Alpha query','reason':'Alias search'},
            {'entity_id':'invalid','query':'invented','reason':'Invalid'},
            *[{'entity_id':'b','query':q,'reason':'Retry'} for q in ['Beta','Beta wrong-country','Beta wrong-country','B2','B3','B4']]])
    wf=GeocoderWorkflow('geocoder',source,set(Category),Settings(web_search_model='test'),proposer)
    ctx=RunContext('batch',tmp_path,30,0)
    asyncio.run(wf.prepare(es,ctx))
    assert len(calls)==1 and len(calls[0]['entities'])==2
    assert ctx.geocoder_results['a'].geometry
    assert not ctx.geocoder_results['b'].geometry
    assert ('B4','BR') not in source.calls
    assert source.calls.count(('Beta wrong-country','BR'))==1
    assert ctx.issues[0].code=='invalid_geocoder_refinement_id'


def test_ambiguous_records_are_not_overridden_by_narrower_query(tmp_path):
    e=location(name='Alpha');e.aliases=['Alpha alias']
    source=Source({'Alpha':[candidate('Alpha','1'),candidate('Alpha','2')],'Alpha alias':[candidate('Alpha','1')]})
    wf=GeocoderWorkflow('geocoder',source,set(Category))
    r=asyncio.run(wf.verify(e,RunContext('ambiguous',tmp_path,20,0)))
    assert r.status=='conflicting' and r.geometry is None


def test_query_failure_is_logged_without_erasing_other_results(tmp_path):
    source=Source({})
    async def proposer(*args):raise ValueError('malformed response')
    wf=GeocoderWorkflow('geocoder',source,set(Category),Settings(web_search_model='test'),proposer)
    ctx=RunContext('error',tmp_path,20,0)
    asyncio.run(wf.prepare([location()],ctx))
    assert ctx.geocoder_results['state'].status=='no_match'
    assert ctx.geocoder_results['state'].issues[0].code=='geocoder_refinement_failed'


def test_model_suggestions_cached_and_accounted_without_web_tools(monkeypatch,tmp_path):
    from types import SimpleNamespace
    from georeferencer.geocoder_refinement import propose_queries
    calls=[]
    class Client:
        def __init__(self,**kwargs):self.responses=self
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def create(self,**kwargs):
            calls.append(kwargs)
            assert 'tools' not in kwargs
            usage=SimpleNamespace(input_tokens=100,output_tokens=50,total_tokens=150,
                model_dump=lambda:{'input_tokens':100,'output_tokens':50,'total_tokens':150})
            return SimpleNamespace(model='gpt-6-luna',status='completed',id='test',usage=usage,
                output_text=json.dumps({'queries':[{'entity_id':'state','query':'Tocantins','reason':'Drop suffix'}]}))
    monkeypatch.setattr('georeferencer.geocoder_refinement.AsyncOpenAI',Client)
    settings=Settings(library_dir=tmp_path,web_search_model='gpt-6-luna')
    request={'semantic_group':'context','entities':[{'entity_id':'state'}]}
    first=RunContext('fresh',tmp_path,0,0)
    asyncio.run(propose_queries(settings,request,first))
    assert first.usage.geocoder_refinement_requests==1 and first.usage.web_requests==0
    assert first.raw_llm_responses[0]['stage']=='geocoder_refinement'
    second=RunContext('cached',tmp_path,0,0)
    asyncio.run(propose_queries(settings,request,second))
    assert len(calls)==1 and second.usage.model_calls==0
