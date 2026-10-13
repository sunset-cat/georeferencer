import pytest
import asyncio
import json
import httpx
from types import SimpleNamespace

from georeferencer.models import Entity, Mention, Category, VerificationClass
from georeferencer.models import Extraction, Point, VerificationResult
from georeferencer.pipeline import Pipeline
from georeferencer.web_resolution import source_urls, validate_candidate
from georeferencer.web_resolution import WebResolution
from georeferencer.config import Settings
from georeferencer.observability import RunContext


def entity():
    return Entity(id="e1", name="Pike Zone", category="mining", subtype="mineralized_zone",
                  role="subject_component", aliases=[], mentions=[Mention(quote="Pike Zone")],
                  identifiers=[], country_code="CA", jurisdiction="Saskatchewan")


def claim():
    return dict(url="https://survey.example/pike", identity_quote="Pike Zone in Saskatchewan.",
                coordinate_quote="Pike Zone representative point WGS84: longitude -105.2 latitude 57.1.",
                longitude=-105.2, latitude=57.1, crs="WGS84", meaning="representative_point")


def test_coordinate_requires_retrieved_entity_specific_evidence():
    c = claim()
    body = c["identity_quote"] + " " + c["coordinate_quote"]
    result = validate_candidate(entity(), c, body)
    assert result.geometry.coordinates == (-105.2, 57.1)
    assert result.independence == "unknown"
    with pytest.raises(ValueError, match="absent"):
        validate_candidate(entity(), c, c["identity_quote"])
    c["coordinate_quote"] = "West McArthur WGS84: -105.2 57.1."
    with pytest.raises(ValueError, match="requested entity"):
        validate_candidate(entity(), c, c["identity_quote"] + c["coordinate_quote"])


def test_identity_only_does_not_invent_geometry():
    c = claim()
    c.update(longitude=None, latitude=None, coordinate_quote=None)
    assert validate_candidate(entity(), c, c["identity_quote"]).geometry is None


def test_projected_source_coordinates_are_retained_without_inventing_wgs84():
    c = claim()
    c.update(longitude=None, latitude=None, crs="SAD 69 / UTM", crs_quote="Surveys use SAD 69 / UTM.",
             coordinate_quote="Pike Zone collar easting 820439.9 northing 8809974.")
    candidate = validate_candidate(entity(), c, c["identity_quote"] + " " + c["coordinate_quote"] + " " + c["crs_quote"])
    assert candidate.geometry is None and candidate.evidence["coordinate_format"] == "projected"
    assert "zone and hemisphere are missing" in candidate.evidence["coordinate_validation"].lower()


def test_labelled_projected_source_coordinates_are_converted_and_auditable():
    c=claim()
    c.update(longitude=None,latitude=None,crs='UTM NAD83 Z13N',crs_quote='(UTM NAD83 Z13N)',
        coordinate_quote='Pike Zone collared at 477,359 mE / 6,396,539 mN (UTM NAD83 Z13N).',meaning='exact_site')
    candidate=validate_candidate(entity(),c,c['identity_quote']+' '+c['coordinate_quote'])
    lon,lat=candidate.geometry.coordinates
    assert -106<lon<-105 and 57<lat<58
    assert candidate.evidence['crs_status']=='transformed'
    metadata=json.loads(candidate.evidence['transformation'])
    assert metadata['source_crs']=='EPSG:26913' and metadata['original_x']==477359
    c['crs_quote']=None
    assert validate_candidate(entity(),c,c['identity_quote']+' '+c['coordinate_quote']).geometry is not None
    c['meaning']='unknown'
    unclear=validate_candidate(entity(),c,c['identity_quote']+' '+c['coordinate_quote'])
    assert unclear.geometry is None and 'spatial meaning' in unclear.evidence['coordinate_validation']
    c['meaning']='exact_site'
    c['crs']='UTM NAD83 Z12N'
    candidate=validate_candidate(entity(),c,c['identity_quote']+' '+c['coordinate_quote'])
    assert candidate.geometry is None and 'contradicts' in candidate.evidence['coordinate_validation']


def test_rejects_wrong_jurisdiction_values_and_crs():
    c = claim()
    c["identity_quote"] = "Pike Zone in Alberta."
    with pytest.raises(ValueError, match="jurisdiction"):
        validate_candidate(entity(), c, "Pike Zone in Alberta.")
    c = claim()
    body = c["identity_quote"] + c["coordinate_quote"]
    c["latitude"] = 58
    with pytest.raises(ValueError, match="values"):
        validate_candidate(entity(), c, body)
    c = claim()
    c["crs"] = "EPSG:26913"
    with pytest.raises(ValueError, match="CRS"):
        validate_candidate(entity(), c, body)


def test_search_source_urls_are_deduplicated():
    assert source_urls([{"action": {"sources": [{"url": "https://survey.example"}]}},
                        {"annotations": [{"url": "https://survey.example", "title": "Survey"}]}]) == [
        {"url": "https://survey.example", "title": "Survey"}]


def test_web_request_validation_cache_and_usage(tmp_path, monkeypatch):
    c = claim()
    calls = []
    usage = SimpleNamespace(input_tokens=10, output_tokens=20, total_tokens=30,
                            model_dump=lambda: {"total_tokens": 30})
    item = SimpleNamespace(model_dump=lambda **kwargs: {"type": "web_search_call",
        "action": {"sources": [{"url": c["url"]}]}})

    class Client:
        async def __aenter__(self):
            self.responses = self
            return self
        async def __aexit__(self, *args):
            pass
        async def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(output=[item], usage=usage, id="resp1", status="completed",
                                   output_text=json.dumps({"candidates": [c]}))

    async def download(*args, **kwargs):
        return (c["identity_quote"] + c["coordinate_quote"]).encode(), "text/plain", c["url"]

    monkeypatch.setattr("georeferencer.web_resolution.AsyncOpenAI", lambda **kwargs: Client())
    monkeypatch.setattr("georeferencer.web_resolution.download_document", download)
    workflow = WebResolution(Settings(model="test", library_dir=tmp_path, source_document_cache_dir=tmp_path / "sources"))
    ctx = RunContext("first", tmp_path, 8, 2)
    result = asyncio.run(workflow.verify(entity(), ctx))
    assert result.status == "supported" and result.geometry.coordinates == (-105.2, 57.1)
    assert not result.independently_confirmed
    assert ctx.usage.total_tokens == 30 and ctx.usage.web_requests == 1
    assert ctx.usage.web_search_tool_calls == 1
    assert calls[0]["tools"] == [{"type": "web_search"}]
    assert ctx.raw_llm_responses[0]["output_text"] == json.dumps({"candidates": [c]})
    replay = RunContext("cached", tmp_path, 0, 0)
    assert asyncio.run(workflow.verify(entity(), replay)).geometry == result.geometry
    assert len(calls) == 1 and replay.usage.model_calls == 0
    assert replay.raw_llm_responses[0]["served_from_cache"]


@pytest.mark.parametrize("failure", ["html_pdf", "network_recovers", "network_fails", "bad_quote"])
def test_source_failures_are_classified_and_retries_are_bounded(tmp_path, monkeypatch, failure):
    c = claim()
    if failure == "html_pdf":
        c["url"] = "https://survey.example/report.pdf"
    usage = SimpleNamespace(input_tokens=10, output_tokens=20, total_tokens=30,
                            model_dump=lambda: {"total_tokens": 30})
    item = SimpleNamespace(model_dump=lambda **kwargs: {"type": "web_search_call",
        "action": {"sources": [{"url": c["url"]}]}})
    class Client:
        async def __aenter__(self):
            self.responses = self
            return self
        async def __aexit__(self, *args):
            pass
        async def create(self, **kwargs):
            return SimpleNamespace(output=[item], usage=usage, id="resp1", status="completed",
                                   output_text=json.dumps({"candidates": [c]}))
    downloads = []
    async def download(*args, **kwargs):
        downloads.append(args[0])
        if failure == "network_fails" or failure == "network_recovers" and len(downloads) == 1:
            raise httpx.ReadTimeout("Source read timed out")
        if failure == "html_pdf":
            return b'<!DOCTYPE html><html>Please wait</html>', "text/html", c["url"]
        if failure == "bad_quote":
            return b'Another site in Saskatchewan', "text/plain", c["url"]
        return (c["identity_quote"] + c["coordinate_quote"]).encode(), "text/plain", c["url"]
    monkeypatch.setattr("georeferencer.web_resolution.AsyncOpenAI", lambda **kwargs: Client())
    monkeypatch.setattr("georeferencer.web_resolution.download_document", download)
    workflow = WebResolution(Settings(model="test", library_dir=tmp_path, source_document_cache_dir=tmp_path / "sources"))
    ctx = RunContext("source-failure", tmp_path, 8, 2)
    result = asyncio.run(workflow.verify(entity(), ctx))
    assert ctx.usage.model_calls == 1
    assert len(downloads) == (2 if failure.startswith("network") else 1)
    if failure == "network_recovers":
        assert result.status == "supported" and not result.issues
        assert result.geometry.coordinates == (-105.2, 57.1)
    else:
        assert result.status == ("no_match" if failure == "bad_quote" else "unavailable")
        issue = result.issues[0]
        assert issue.code == {"html_pdf": "web_source_parse_failed", "network_fails": "web_source_download_failed",
                              "bad_quote": "web_evidence_rejected"}[failure]
        assert c["url"] in issue.message
        assert issue.retryable == (failure == "network_fails")
        assert not list((tmp_path / "web-cache").glob("*.json"))


def test_pipeline_distinguishes_supported_identity_from_missing_geometry(tmp_path):
    class Extractor:
        async def extract(self, document, ctx, feedback):
            return Extraction(entities=[entity()])
    class Source:
        name = "identity_only"
        def supports(self, entity):
            return True
        async def verify(self, entity, ctx):
            return VerificationResult(workflow=self.name, status="supported", identity="supported")
    result = asyncio.run(Pipeline(Settings(log_dir=tmp_path), Extractor(), [Source()]).run("report", "Pike Zone"))
    assert result.entities[0].status == "unresolved"
    assert "identity supported" in result.entities[0].reason
    assert result.geojson["features"] == []


@pytest.mark.parametrize("mining", [True, False])
@pytest.mark.parametrize("direct_status", ["confirmed", "no_match", "conflicting", "unavailable"])
def test_pipeline_web_search_depends_on_geocoder_and_mining_status(tmp_path, mining, direct_status):
    extracted = entity()
    if not mining:
        extracted.category, extracted.subtype = Category.geographic, "settlement"
    class Extractor:
        async def extract(self, document, ctx, feedback):
            return Extraction(entities=[extracted])
    class Direct:
        name = "geocoder"
        def supports(self, entity):
            return True
        async def verify(self, entity, ctx):
            return VerificationResult(workflow=self.name, status=direct_status,
                identity="confirmed" if direct_status == "confirmed" else "unverified",
                geometry=Point(coordinates=(-105, 57)) if direct_status == "confirmed" else None,
                geometry_basis="exact_site" if direct_status == "confirmed" else None)
    class Fallback(Direct):
        name = "web_search"
        fallback = True
        calls = 0
        async def verify(self, entity, ctx):
            self.calls += 1
            return VerificationResult(workflow=self.name, status="no_match")
    fallback = Fallback()
    engine = Pipeline(Settings(log_dir=tmp_path), Extractor(), [Direct(), fallback])
    result = asyncio.run(engine.run("report", "Pike Zone"))
    expect_search = mining or direct_status != "confirmed"
    assert bool(fallback.calls) == expect_search
    assert [r.workflow for r in result.entities[0].verification] == (["geocoder", "web_search"] if expect_search else ["geocoder"])


def test_mining_verification_metadata_triggers_search_despite_geocoder_match():
    from georeferencer.pipeline import web_search_decision
    e = entity()
    e.category, e.subtype = Category.other, "facility"
    e.verification_classes = [VerificationClass.mineral_site]
    match = VerificationResult(workflow="geocoder", status="confirmed", identity="confirmed",
                               geometry=Point(coordinates=(-105, 57)), geometry_basis="representative_point")
    assert web_search_decision(e, [match])[0]


def test_geocoder_identity_without_coordinates_still_needs_search():
    from georeferencer.pipeline import web_search_decision
    e = entity()
    e.category, e.subtype = Category.geographic, "settlement"
    identity_only = VerificationResult(workflow="geocoder", status="confirmed", identity="confirmed")
    assert web_search_decision(e, [identity_only])[0]


def test_mining_geocoder_and_web_disagreement_is_retained_as_conflict(tmp_path):
    class Extractor:
        async def extract(self, document, ctx, feedback):
            return Extraction(entities=[entity()])
    class Geocoder:
        name = "geocoder"
        def supports(self, entity):
            return True
        async def verify(self, entity, ctx):
            return VerificationResult(workflow=self.name, status="confirmed", identity="confirmed",
                                     geometry=Point(coordinates=(-105, 57)), geometry_basis="representative_point")
    class Search(Geocoder):
        name = "web_search"
        async def verify(self, entity, ctx):
            return VerificationResult(workflow=self.name, status="supported", identity="supported",
                                     geometry=Point(coordinates=(-106, 57)), geometry_basis="representative_point")
    result = asyncio.run(Pipeline(Settings(log_dir=tmp_path), Extractor(), [Geocoder(), Search()]).run("report", "Pike Zone"))
    assert result.entities[0].status == "conflicting"
    assert len(result.entities[0].verification) == 2
    assert result.geojson["features"] == []
