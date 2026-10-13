import asyncio
import json
from types import SimpleNamespace

from georeferencer.config import Settings
from georeferencer.models import Entity,Extraction
from georeferencer.pipeline import Pipeline
from georeferencer.project_discovery import build_groups
from georeferencer.observability import RunContext


def location(identifier,name,project=None,primary=False):
    return Entity(id=identifier,name=name,category='mining',subtype='project' if primary else 'drill_hole',
        role='primary_subject' if primary else 'subject_component',aliases=[],identifiers=[],
        country_code='CA',jurisdiction='Saskatchewan',project_ids=[project] if project else [],
        mentions=[{'quote':name}])


def setup(monkeypatch,tmp_path,entities,**limits):
    tamper=limits.pop('tamper_verification',None)
    calls,downloads=[],[]
    coordinates={e.id:(-105.2-index*.001,57.1+index*.001) for index,e in enumerate(entities)}
    quotes={e.id:f'{e.name} in Saskatchewan: longitude {coordinates[e.id][0]}, latitude {coordinates[e.id][1]}.' for e in entities}
    bodies={}
    class Client:
        async def __aenter__(self):self.responses=self;return self
        async def __aexit__(self,*args):pass
        async def create(self,**parameters):
            request=json.loads(parameters['input'][1]['content']);discovery='tools' in parameters
            calls.append((discovery,request,parameters))
            if discovery:
                url=f"https://operator.example/{request['project_id']}.txt"
                bodies[url]='Coordinates use WGS84.\n'+'\n'.join(quotes[e['entity_id']] for e in request['entities'])
                payload={'documents':[{'url':url,'title':'Project technical report','entity_ids':request['requested_entity_ids']}],
                    'candidates':[],'note':'Shared report for the requested project.'}
                output=[SimpleNamespace(model_dump=lambda **kw:{'type':'web_search_call','status':'completed',
                    'action':{'type':'search','sources':[{'url':url}]}})]
            else:
                url=request['sources'][0]['url']
                payload={'candidates':[{'entity_id':e['entity_id'],'name':e['name'],'url':url,
                    'identity_quote':quotes[e['entity_id']], 'coordinate_format':'decimal',
                    'longitude':coordinates[e['entity_id']][0],'latitude':coordinates[e['entity_id']][1],
                    'coordinate_quotes':[quotes[e['entity_id']]],'header_quotes':[],
                    'crs':'WGS84','crs_quotes':['Coordinates use WGS84.'],'meaning':'exact_site'} for e in request['entities']]}
                output=[]
                if tamper:payload=tamper(payload)
            usage=SimpleNamespace(input_tokens=100,output_tokens=50,total_tokens=150,
                model_dump=lambda:{'input_tokens':100,'output_tokens':50,'total_tokens':150})
            return SimpleNamespace(id=f'response-{len(calls)}',model=parameters['model'],status='completed',
                output=output,usage=usage,output_text=json.dumps(payload))
    async def download(url,*args,**kw):
        downloads.append(url)
        return bodies[url].encode(),'text/plain',url
    monkeypatch.setattr('georeferencer.web_resolution.AsyncOpenAI',lambda **kw:Client())
    monkeypatch.setattr('georeferencer.download.download_document',download)
    class Extractor:
        async def extract(self,*args):return Extraction(entities=[e.model_copy(deep=True) for e in entities])
    settings=Settings(model='gpt-6.1-sol',web_search_model='gpt-6-luna',verification_model='gpt-6.1-sol',
        geographic_audit_enabled=False,
        web_search_enabled=True,library_dir=tmp_path/'library',source_document_cache_dir=tmp_path/'sources',
        log_dir=tmp_path/'logs',**limits)
    pipeline=Pipeline(settings,Extractor())
    text=' '.join(e.name for e in entities)
    return pipeline,text,calls,downloads,coordinates


def test_one_discovery_includes_all_25_holes_and_reuses_one_download(monkeypatch,tmp_path):
    entities=[location('project','Alpha project',primary=True),
        *[location(f'hole-{i}',f'DD-{i:03d}','project') for i in range(25)]]
    pipeline,text,calls,downloads,coordinates=setup(monkeypatch,tmp_path,entities)
    result=asyncio.run(pipeline.run('report',text))
    discovery=[c for c in calls if c[0]]
    assert len(discovery)==result.usage.web_requests==1
    assert len(discovery[0][1]['entities'])==26
    assert {e['entity_id'] for e in discovery[0][1]['entities']}==set(coordinates)
    assert len(downloads)==1 and result.usage.verification_requests==5
    assert result.progress['resolved']==26 and result.status=='completed'
    raw=next(r for r in result.raw_llm_responses if r['stage']=='web_search')
    assert raw['project_id']=='project' and len(raw['entity_ids'])==26
    # Replay model responses and the downloaded report without fresh provider calls.
    old_count=len(calls)
    replay=asyncio.run(pipeline.run('report',text))
    assert len(calls)==old_count and len(downloads)==1
    assert replay.progress['resolved']==26 and replay.usage.web_requests==0


def test_two_projects_in_same_region_keep_identical_hole_names_separate(monkeypatch,tmp_path):
    entities=[location('alpha','Alpha project',primary=True),location('beta','Beta project',primary=True),
        location('alpha-hole','DD-001','alpha'),location('beta-hole','DD-001','beta')]
    pipeline,text,calls,downloads,coordinates=setup(monkeypatch,tmp_path,entities)
    result=asyncio.run(pipeline.run('report',text))
    discovery=[c[1] for c in calls if c[0]]
    assert result.progress['resolved']==4 and result.usage.web_requests==2
    assert {r['project_id']:set(e['entity_id'] for e in r['entities']) for r in discovery}=={
        'alpha':{'alpha','alpha-hole'},'beta':{'beta','beta-hole'}}
    holes=[r for r in result.entities if r.entity.subtype=='drill_hole']
    assert {r.entity.id:r.geometry.coordinates for r in holes}=={i:coordinates[i] for i in ['alpha-hole','beta-hole']}


def test_discovery_budget_failure_is_shared_without_per_entity_retries(monkeypatch,tmp_path):
    entities=[location('project','Alpha project',primary=True),*[location(f'hole-{i}',f'DD-{i:03d}','project') for i in range(4)]]
    pipeline,text,calls,downloads,_=setup(monkeypatch,tmp_path,entities,max_web_requests=0)
    result=asyncio.run(pipeline.run('report',text))
    assert not calls and not downloads
    assert result.progress['pending']==5
    events=[json.loads(l) for l in next((tmp_path/'logs').glob('*.jsonl')).read_text().splitlines()]
    assert sum(e['event']=='web.project_discovery_started' for e in events)==1


def test_verification_budget_preserves_first_batch_and_marks_remaining_pending(monkeypatch,tmp_path):
    entities=[location('project','Alpha project',primary=True),*[location(f'hole-{i}',f'DD-{i:03d}','project') for i in range(8)]]
    pipeline,text,calls,downloads,_=setup(monkeypatch,tmp_path,entities,max_verification_requests=1)
    result=asyncio.run(pipeline.run('report',text))
    assert result.usage.web_requests==1 and result.usage.verification_requests==1
    assert result.progress['resolved']==6 and result.progress['pending']==3


def test_unknown_association_is_not_guessed_between_two_primary_projects(tmp_path):
    entities=[location('a','Alpha',primary=True),location('b','Beta',primary=True),location('hole','DD-001')]
    ctx=RunContext('groups',tmp_path,0,0)
    groups,membership=build_groups(entities,entities,ctx)
    assert membership['hole']==['hole'] and len(groups)==3


def test_shared_location_can_belong_to_two_projects(monkeypatch,tmp_path):
    shared=Entity(id='river',name='River Example',category='general_geographic',subtype='river',
        role='context_location',aliases=[],identifiers=[],country_code='CA',jurisdiction='Saskatchewan',
        project_ids=['alpha','beta'],mentions=[{'quote':'River Example'}])
    entities=[location('alpha','Alpha project',primary=True),location('beta','Beta project',primary=True),shared]
    pipeline,text,calls,_,coordinates=setup(monkeypatch,tmp_path,entities)
    result=asyncio.run(pipeline.run('report',text,include_context=True))
    discovery=[c[1] for c in calls if c[0]]
    assert len(discovery)==2 and all('river' in r['requested_entity_ids'] for r in discovery)
    river=next(r for r in result.entities if r.entity.id=='river')
    assert river.status=='resolved' and river.geometry.coordinates==coordinates['river']
    assert len(river.verification[0].sources)==2


def test_bad_candidate_does_not_discard_other_verified_entities(monkeypatch,tmp_path):
    entities=[location('project','Alpha project',primary=True),location('hole','DD-001','project')]
    def tamper(payload):
        payload['candidates'].extend(['malformed',{'entity_id':'unknown'},{'entity_id':['unknown']}])
        return payload
    pipeline,text,calls,_,_=setup(monkeypatch,tmp_path,entities,tamper_verification=tamper)
    result=asyncio.run(pipeline.run('report',text))
    assert result.progress['resolved']==2 and result.usage.web_requests==1
    assert any(i.code=='web_evidence_rejected' for i in result.issues)


def test_cross_border_region_is_a_shared_semantic_unit(tmp_path):
    region=Entity(id='pyrenees',name='Pyrenees',category='geological',subtype='geological_unit',
        role='primary_subject',aliases=[],identifiers=[],country_code=None,jurisdiction=None,
        mentions=[{'quote':'Pyrenees'}])
    members=[]
    for identifier,name,country in [('fr','French locality','FR'),('es','Spanish locality','ES')]:
        members.append(Entity(id=identifier,name=name,category='general_geographic',subtype='settlement',
            role='subject_component',aliases=[],identifiers=[],country_code=country,jurisdiction=None,
            project_ids=['pyrenees'],mentions=[{'quote':name}]))
    ctx=RunContext('regional-groups',tmp_path,0,0)
    groups,membership=build_groups([region,*members],[region,*members],ctx)
    assert set(groups)=={'pyrenees'}
    assert membership=={'pyrenees':['pyrenees'],'fr':['pyrenees'],'es':['pyrenees']}
    assert not ctx.issues
