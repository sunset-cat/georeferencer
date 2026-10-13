import asyncio
import json
from types import SimpleNamespace

import httpx

from georeferencer.config import Settings
from georeferencer.models import Entity, Mention
from georeferencer.observability import RunContext
from georeferencer.source_documents import SourceDocumentCache
from georeferencer.web_resolution import WebResolution


URL = "https://survey.example/pike"
CLAIM = dict(name="Pike Zone", url=URL, identity_quote="Pike Zone in Saskatchewan.",
             coordinate_quote="Pike Zone WGS84 longitude -105.2 latitude 57.1.",
             longitude=-105.2, latitude=57.1, crs="WGS84", meaning="representative_point")
TEXT = CLAIM["identity_quote"] + " " + CLAIM["coordinate_quote"]


def settings(tmp_path):
    return Settings(model="gpt-6.1-sol", reasoning_effort="low", web_search_model="gpt-6-luna",
                    web_search_reasoning_effort="low", verification_model="gpt-6.1-sol",
                    verification_reasoning_effort="low", library_dir=tmp_path / "library",
                    source_document_cache_dir=tmp_path / "documents")


def entity():
    return Entity(id="e1", name="Pike Zone", category="mining", subtype="mineralized_zone",
                  role="primary_subject", mentions=[{"quote": "Pike Zone"}], jurisdiction="Saskatchewan",
                  aliases=[], identifiers=[], country_code="CA")


def model_mock(monkeypatch, calls):
    class Client:
        async def __aenter__(self):
            self.responses = self
            return self
        async def __aexit__(self, *args):
            pass
        async def create(self, **kwargs):
            calls.append(kwargs)
            searching = "tools" in kwargs
            assert kwargs["model"] == ("gpt-6-luna" if searching else "gpt-6.1-sol")
            assert kwargs["reasoning"] == {"effort": "low"}
            output = [{"type": "web_search_call", "status": "completed", "action": {"type": "search", "sources": [{"url": URL}]}}] if searching else []
            if not searching:
                request = json.loads(kwargs["input"][1]["content"])
                assert request["sources"][0]["url"] == URL
                assert len(kwargs["input"][1]["content"]) < 13000
            usage = dict(input_tokens=100, output_tokens=50, total_tokens=150,
                         input_tokens_details={"cached_tokens": 0, "cache_write_tokens": 0})
            return SimpleNamespace(output=[SimpleNamespace(model_dump=lambda **kw: item) for item in output],
                usage=SimpleNamespace(**usage, model_dump=lambda: usage), model=kwargs["model"],
                id=f"response-{len(calls)}", status="completed", output_text=json.dumps({"candidates": [CLAIM]}))
    monkeypatch.setattr("georeferencer.web_resolution.AsyncOpenAI", lambda **kwargs: Client())


def test_cached_report_uses_sol_without_web_search_and_replays_after_restart(tmp_path, monkeypatch):
    calls = []
    model_mock(monkeypatch, calls)
    async def download(*args, **kwargs):
        return TEXT.encode(), "text/plain", URL
    s = settings(tmp_path)
    async def prepare():
        cache = SourceDocumentCache(s.source_document_cache_dir)
        doc = await cache.get(URL, 10000, 5, downloader=download)
        await cache.parse(doc, 1000)
    asyncio.run(prepare())
    first = RunContext("first", tmp_path, 5, 2)
    result = asyncio.run(WebResolution(s).verify(entity(), first))
    assert result.geometry.coordinates == (-105.2, 57.1)
    assert len(calls) == 1 and "tools" not in calls[0]
    assert first.usage.web_requests == 0 and first.usage.verification_requests == 1
    assert first.raw_llm_responses[0]["stage"] == "web_verification"
    second = RunContext("second", tmp_path, 0, 0)
    replay = asyncio.run(WebResolution(s).verify(entity(), second))
    assert replay.geometry == result.geometry and len(calls) == 1
    assert second.usage.model_calls == 0 and second.raw_llm_responses[0]["served_from_cache"]


def test_failed_download_reuses_luna_response_then_sol_checks_recovered_source(tmp_path, monkeypatch):
    calls, downloads = [], []
    model_mock(monkeypatch, calls)
    async def download(url, *args, **kwargs):
        downloads.append(url)
        if len(downloads) == 1:
            request = httpx.Request("GET", url)
            raise httpx.HTTPStatusError("404 Not Found", request=request, response=httpx.Response(404, request=request))
        return TEXT.encode(), "text/plain", URL
    monkeypatch.setattr("georeferencer.web_resolution.download_document", download)
    s = settings(tmp_path)
    first = RunContext("first", tmp_path, 5, 2)
    failed = asyncio.run(WebResolution(s).verify(entity(), first))
    assert failed.status == "unavailable" and len(calls) == 1
    assert list((s.library_dir / "web-response-cache").glob("*.json"))
    # A permanent 404 is cached briefly; a later recovery is checked after expiry.
    for path in s.source_document_cache_dir.glob('*/failure.json'):
        saved=json.loads(path.read_text()); saved['timestamp']=0
        path.write_text(json.dumps(saved))
    second = RunContext("second", tmp_path, 5, 2)
    recovered = asyncio.run(WebResolution(s).verify(entity(), second))
    assert recovered.geometry.coordinates == (-105.2, 57.1)
    assert len(calls) == 2 and len(downloads) == 2
    assert second.usage.web_requests == 0 and second.usage.verification_requests == 1
    assert [r["stage"] for r in second.raw_llm_responses] == ["web_search", "web_verification"]
    assert second.raw_llm_responses[0]["served_from_cache"]


def test_verification_budget_stops_search_spend_when_verification_cannot_run(tmp_path, monkeypatch):
    from dataclasses import replace
    calls = []
    model_mock(monkeypatch, calls)
    ctx = RunContext("limited", tmp_path, 5, 2)
    result = asyncio.run(WebResolution(replace(settings(tmp_path), max_verification_requests=0)).verify(entity(), ctx))
    assert not calls and ctx.usage.web_requests == 0
    assert result.issues[0].code == "verification_budget_exhausted"


def test_two_entities_share_one_source_verification_call(tmp_path, monkeypatch):
    second=entity().model_copy(deep=True); second.id='e2'; second.name='East Zone'; second.mentions=[Mention(quote='East Zone')]
    text=TEXT+' East Zone in Saskatchewan. East Zone WGS84 longitude -105.3 latitude 57.2.'
    calls=[]
    class Client:
        async def __aenter__(self): self.responses=self; return self
        async def __aexit__(self,*args): pass
        async def create(self,**parameters):
            calls.append(parameters)
            assert 'tools' not in parameters
            request=json.loads(parameters['input'][1]['content'])
            assert len(request['entities'])==2
            claims=[{**CLAIM,'entity_id':'e1'},{**CLAIM,'entity_id':'e2','name':'East Zone',
                     'identity_quote':'East Zone in Saskatchewan.','coordinate_quote':'East Zone WGS84 longitude -105.3 latitude 57.2.',
                     'longitude':-105.3,'latitude':57.2}]
            usage=SimpleNamespace(input_tokens=100,output_tokens=100,total_tokens=200,model_dump=lambda:{'input_tokens':100,'output_tokens':100})
            return SimpleNamespace(output=[],usage=usage,id='batch',status='completed',output_text=json.dumps({'candidates':claims}))
    monkeypatch.setattr('georeferencer.web_resolution.AsyncOpenAI',lambda **kwargs:Client())
    s=settings(tmp_path); workflow=WebResolution(s)
    async def download(*args,**kwargs):return text.encode(),'text/plain',URL
    async def run():
        document=await workflow.documents.get(URL,100000,30,downloader=download)
        await workflow.documents.parse(document,1000)
        ctx=RunContext('batch',tmp_path,5,2)
        await workflow.prepare([entity(),second],ctx)
        first=await workflow.verify(entity(),ctx); other=await workflow.verify(second,ctx)
        assert first.geometry.coordinates==(-105.2,57.1)
        assert other.geometry.coordinates==(-105.3,57.2)
        assert ctx.usage.model_calls==1 and ctx.usage.web_requests==0
        assert ctx.raw_llm_responses[0]['entity_ids']==['e1','e2']
    asyncio.run(run())
    assert len(calls)==1


def test_dead_proposed_source_falls_back_to_another_returned_source(tmp_path,monkeypatch):
    bad='https://survey.example/dead.pdf'; downloaded=[]
    class Client:
        async def __aenter__(self):self.responses=self;return self
        async def __aexit__(self,*args):pass
        async def create(self,**parameters):
            searching='tools' in parameters
            output=[SimpleNamespace(model_dump=lambda **kwargs:{'type':'web_search_call','action':{'sources':[{'url':bad},{'url':URL,'title':'Technical report'}]}})] if searching else []
            usage=SimpleNamespace(input_tokens=100,output_tokens=50,total_tokens=150,model_dump=lambda:{'input_tokens':100,'output_tokens':50})
            claim={**CLAIM,'url':bad} if searching else CLAIM
            return SimpleNamespace(output=output,usage=usage,id='fallback',status='completed',output_text=json.dumps({'candidates':[claim]}))
    async def download(url,*args,**kwargs):
        downloaded.append(url)
        if url==bad:
            request=httpx.Request('GET',url)
            raise httpx.HTTPStatusError('404',request=request,response=httpx.Response(404,request=request))
        return TEXT.encode(),'text/plain',URL
    monkeypatch.setattr('georeferencer.web_resolution.AsyncOpenAI',lambda **kwargs:Client())
    monkeypatch.setattr('georeferencer.web_resolution.download_document',download)
    ctx=RunContext('fallback',tmp_path,5,2)
    result=asyncio.run(WebResolution(settings(tmp_path)).verify(entity(),ctx))
    assert result.geometry.coordinates==(-105.2,57.1)
    assert downloaded==[bad,URL] and ctx.usage.web_requests==1


def test_rejected_verification_response_is_cached_without_repeating_paid_calls(tmp_path, monkeypatch):
    calls = []
    model_mock(monkeypatch, calls)
    monkeypatch.setitem(CLAIM, "coordinate_quote", "Pike Zone WGS84 longitude -105.2 latitude 57.1. Invented sentence.")
    async def download(*args, **kwargs):
        return TEXT.encode(), "text/plain", URL
    monkeypatch.setattr("georeferencer.web_resolution.download_document", download)
    s = settings(tmp_path)
    first = asyncio.run(WebResolution(s).verify(entity(), RunContext("first", tmp_path, 5, 2)))
    assert first.geometry is None and first.issues[0].code == "web_evidence_rejected"
    replay = RunContext("replay", tmp_path, 0, 0)
    second = asyncio.run(WebResolution(s).verify(entity(), replay))
    assert second.geometry is None and second.issues
    assert len(calls) == 2 and replay.usage.model_calls == 0
