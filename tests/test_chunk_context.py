import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace

from georeferencer.chunked_extraction import ChunkedExtractor
from georeferencer.config import Settings
from georeferencer.extraction import OpenAIExtractor
from georeferencer.models import Document, Entity, Extraction
from georeferencer.observability import RunContext
from georeferencer.validation import evidence_errors


def location(identifier,name,country=None,region=None,**changes):
    return Entity(id=identifier,name=name,category='mining',subtype='project',
        role='primary_subject',aliases=[],identifiers=[],country_code=country,
        jurisdiction=region,mentions=[{'quote':name}],**changes)


def chunks(monkeypatch,parts):
    positions=[]
    start=0
    for part in parts:
        positions.append((start,start+len(part),part))
        start+=len(part)
    monkeypatch.setattr('georeferencer.chunked_extraction.split_text',lambda *a,**kw:positions)
    return Document(name='report',text=''.join(parts))


def test_forward_memory_merges_mentions_coords_and_keeps_multiple_regions(monkeypatch,tmp_path):
    parts=['Ridge in Alberta, Canada. Mesa in Tocantins, Brazil.\n',
        'Ridge target. WGS84 longitude -110.0 latitude 54.0.\n','Mesa drilling programme.\n']
    document=chunks(monkeypatch,parts)
    contexts=[]
    class Extractor:
        calls=0
        async def extract(self,doc,ctx,feedback):
            self.calls+=1
            ctx.consume('model')
            contexts.append(deepcopy(ctx.extraction_context))
            if ctx.chunk_index==1:
                return Extraction(entities=[location('ridge','Ridge','CA','Alberta'),location('mesa','Mesa','BR','Tocantins')])
            if ctx.chunk_index==2:
                return Extraction(entities=[location('c1-ridge','Ridge',coordinate_claims=[{
                    'longitude':-110,'latitude':54,'crs':'WGS84','meaning':'representative_point',
                    'evidence':{'quote':parts[1].strip()},'crs_evidence':{'quote':'WGS84'}}])])
            return Extraction(entities=[location('c1-mesa','Mesa')])
    settings=Settings(model='fixture',library_dir=tmp_path,log_dir=tmp_path)
    delegate=Extractor()
    first=RunContext('first',tmp_path,0,6)
    result=asyncio.run(ChunkedExtractor(delegate,settings).extract(document,first,[]))
    assert len(result.entities)==2 and delegate.calls==3
    assert contexts[0]=={'existing_locations':[],'geographic_contexts':[]}
    assert {(c['country_code'],c['jurisdiction']) for c in contexts[1]['geographic_contexts']}=={('CA','Alberta'),('BR','Tocantins')}
    assert contexts[1]['geographic_contexts'][0]['applies_to']==['c1-ridge']
    assert [e['id'] for e in contexts[2]['existing_locations']]==['c1-ridge','c1-mesa']
    assert all('coordinate_claims' not in e for e in contexts[2]['existing_locations'])
    ridge=result.entities[0]
    assert len(ridge.mentions)==2 and len(ridge.coordinate_claims)==1
    assert ridge.country_code=='CA' and ridge.jurisdiction=='Alberta'
    assert ridge.coordinate_claims[0].crs_evidence.start==document.text.index('WGS84')
    assert not evidence_errors(result,document)
    replay_ctx=RunContext('replay',tmp_path,0,6)
    replay=asyncio.run(ChunkedExtractor(delegate,settings).extract(document,replay_ctx,[]))
    assert delegate.calls==3 and replay_ctx.usage.model_calls==0
    assert result.model_dump()==replay.model_dump()


def test_conflicting_existing_reference_is_kept_separate(monkeypatch,tmp_path):
    document=chunks(monkeypatch,['Ridge in Alberta.\n','Ridge in Tocantins.\n'])
    class Extractor:
        async def extract(self,doc,ctx,feedback):
            entity=location('ridge','Ridge','CA','Alberta') if ctx.chunk_index==1 else location('c1-ridge','Ridge','BR','Tocantins')
            return Extraction(entities=[entity])
    settings=Settings(library_dir=tmp_path,log_dir=tmp_path)
    ctx=RunContext('conflict',tmp_path,0,4)
    result=asyncio.run(ChunkedExtractor(Extractor(),settings).extract(document,ctx,[]))
    assert len(result.entities)==2
    assert result.entities[0].country_code=='CA' and result.entities[1].country_code=='BR'
    assert any(i.code=='chunk_entity_reference_conflict' for i in ctx.issues)


def test_model_receives_and_records_exact_chunk_context(monkeypatch,tmp_path):
    captured={}
    class Client:
        def __init__(self,**kw):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
    async def response(client,parameters,*args,**kw):
        captured.update(json.loads(parameters['input'][1]['content']))
        return SimpleNamespace(usage=None,id='fixture',status='completed',output_text='{"entities":[]}',model='fixture')
    monkeypatch.setattr('georeferencer.extraction.AsyncOpenAI',Client)
    monkeypatch.setattr('georeferencer.model_retry.response_with_retry',response)
    ctx=RunContext('request',tmp_path,0,2)
    ctx.extraction_context={'existing_locations':[{'id':'known','name':'Ridge'}],'geographic_contexts':[]}
    result=asyncio.run(OpenAIExtractor(Settings(model='fixture')).extract(Document(name='report',text='Ridge'),ctx,[]))
    assert not result.entities
    assert captured['chunk_context']==ctx.extraction_context
    assert ctx.raw_llm_responses[0]['chunk_context']==ctx.extraction_context


def test_later_cache_depends_on_prior_context(monkeypatch,tmp_path):
    document=chunks(monkeypatch,['Ridge in Alberta, with British Columbia discussed.\n','Mesa exploration.\n'])
    calls=[]
    class Extractor:
        async def extract(self,doc,ctx,feedback):
            calls.append((ctx.chunk_index,deepcopy(ctx.extraction_context)))
            entity=location('ridge','Ridge','CA','Alberta') if ctx.chunk_index==1 else location('mesa','Mesa')
            return Extraction(entities=[entity])
    settings=Settings(model='fixture',library_dir=tmp_path,log_dir=tmp_path)
    first=RunContext('cache-first',tmp_path,0,4)
    asyncio.run(ChunkedExtractor(Extractor(),settings).extract(document,first,[]))
    assert len(calls)==2
    # Simulate corrected early-chunk context while keeping the input and model
    # unchanged. A later extraction generated with Alberta must not be reused.
    for path in (tmp_path/'extraction-cache').glob('*.json'):
        if path.name.endswith('.raw.json'):continue
        data=json.loads(path.read_text(encoding='utf-8'))
        if data['entities'][0]['name']=='Ridge':
            data['entities'][0]['jurisdiction']='British Columbia'
            path.write_text(json.dumps(data),encoding='utf-8')
    second=RunContext('cache-second',tmp_path,0,4)
    result=asyncio.run(ChunkedExtractor(Extractor(),settings).extract(document,second,[]))
    assert len(calls)==3 and calls[-1][0]==2
    assert calls[-1][1]['geographic_contexts'][0]['jurisdiction']=='British Columbia'
    assert result.entities[1].country_code is None and result.entities[1].jurisdiction is None


def test_project_references_are_mapped_and_preserved_across_chunks(monkeypatch,tmp_path):
    document=chunks(monkeypatch,['Ridge project and DD-001.\n','DD-001 continued drilling.\n'])
    class Extractor:
        async def extract(self,doc,ctx,feedback):
            if ctx.chunk_index==1:
                project=location('project','Ridge','CA','Alberta')
                drill=Entity(id='hole',name='DD-001',category='mining',subtype='drill_hole',role='subject_component',
                    aliases=[],identifiers=[],country_code='CA',jurisdiction='Alberta',project_ids=['project'],mentions=[{'quote':'DD-001'}])
                return Extraction(entities=[project,drill])
            existing=ctx.extraction_context['existing_locations']
            assert next(e for e in existing if e['name']=='DD-001')['project_ids']==['c1-project']
            return Extraction(entities=[Entity(id='c1-hole',name='DD-001',category='mining',subtype='drill_hole',
                role='subject_component',aliases=[],identifiers=[],country_code=None,jurisdiction=None,
                project_ids=['c1-project'],mentions=[{'quote':'DD-001'}])])
    settings=Settings(library_dir=tmp_path,log_dir=tmp_path)
    result=asyncio.run(ChunkedExtractor(Extractor(),settings).extract(document,RunContext('project-refs',tmp_path,0,4),[]))
    assert len(result.entities)==2 and result.entities[1].project_ids==['c1-project']
    assert len(result.entities[1].mentions)==2
