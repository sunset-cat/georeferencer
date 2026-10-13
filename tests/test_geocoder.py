import asyncio
import time

import httpx
import pytest

from georeferencer.models import Entity, Category
from georeferencer.sources import PhotonSource, NominatimSource

HTTP_CLIENT = httpx.AsyncClient


def entity(name="Berlin"):
    return Entity(id="city", name=name, category="general_geographic", subtype="settlement", role="primary_subject",
                  aliases=[], mentions=[{"quote": name}], identifiers=[], country_code="DE", jurisdiction="Berlin")


def response(name="Berlin"):
    return {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [13.388, 52.517]},
        "properties": {"name": name, "state": "Berlin", "countrycode": "DE", "osm_type": "R", "osm_id": 62422, "osm_key": "place", "osm_value": "city"}}]}


def mock_client(monkeypatch, handler):
    monkeypatch.setattr("georeferencer.sources.httpx.AsyncClient", lambda **kwargs: HTTP_CLIENT(transport=httpx.MockTransport(handler), **kwargs))


def test_photon_candidate_cache_and_record_url(tmp_path, monkeypatch):
    calls = []
    def handler(request):
        calls.append(request)
        assert request.url.params["countrycode"] == "DE"
        assert request.url.params["limit"] == "20"
        assert request.headers["User-Agent"] == "georeferencer-test"
        return httpx.Response(200, json=response())
    mock_client(monkeypatch, handler)
    cache = tmp_path / "cache.sqlite3"
    first = PhotonSource("https://photon.test/api/", "georeferencer-test", 5, cache_path=cache)
    records = asyncio.run(first.search(entity()))
    assert records[0].geometry.coordinates == (13.388, 52.517)
    assert records[0].source_record_url == "https://www.openstreetmap.org/relation/62422"
    assert records[0].independence == "unknown"
    assert not first.last_cache_hit
    # Persist across source instances and process restarts.
    second = PhotonSource("https://photon.test/api/", "georeferencer-test", 5, cache_path=cache)
    assert asyncio.run(second.search(entity())) == records
    assert second.last_cache_hit
    assert len(calls) == 1
    asyncio.run(second.search(entity(), refined=True))
    assert len(calls) == 2
    assert calls[0].url.params["q"] == "Berlin"
    assert calls[1].url.params["q"] == "Berlin, Berlin"
    assert not second.last_cache_hit


def test_concurrent_geocoder_calls_are_serialized_and_spaced(monkeypatch):
    starts = []
    def handler(request):
        starts.append(time.monotonic())
        return httpx.Response(200, json=response(request.url.params["q"]))
    mock_client(monkeypatch, handler)
    source = PhotonSource("https://photon.test/api/", "test", 5, min_interval=.05)
    async def run():
        return await asyncio.gather(source.search(entity()), source.search(entity("Munich")))
    asyncio.run(run())
    assert len(starts) == 2
    assert starts[1] - starts[0] >= .045


def test_repeated_concurrent_query_only_uses_one_request(monkeypatch):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=response())
    mock_client(monkeypatch, handler)
    source = PhotonSource("https://photon.test/api/", "test", 5)
    async def run():
        await asyncio.gather(source.search(entity()), source.search(entity()))
    asyncio.run(run())
    assert len(calls) == 1


def test_http_failures_and_invalid_responses_are_not_cached(monkeypatch):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(429 if len(calls) == 1 else 200, json=response())
    mock_client(monkeypatch, handler)
    source = PhotonSource("https://photon.test/api/", "test", 5, min_interval=0)
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(source.search(entity()))
    assert len(asyncio.run(source.search(entity()))) == 1
    assert len(calls) == 2
    mock_client(monkeypatch, lambda request: httpx.Response(200, json={"unexpected": []}))
    bad = PhotonSource("https://photon.test/api/", "test", 5, min_interval=0)
    with pytest.raises(ValueError, match="invalid feature collection"):
        asyncio.run(bad.search(entity()))
    assert bad.cache == {}


@pytest.mark.parametrize("provider", [PhotonSource, NominatimSource])
@pytest.mark.parametrize("provider_type", ["mine", "quarry"])
def test_geocoder_retains_mining_and_unmapped_results(monkeypatch, provider, provider_type):
    data = response("Example Mine")
    data["features"][0]["properties"]["osm_value"] = provider_type
    nominatim = [{"name": "Example Mine", "display_name": "Example Mine, Berlin, Germany", "type": provider_type,
                  "lat": "52.517", "lon": "13.388", "place_id": 1, "osm_type": "node", "osm_id": 2,
                  "address": {"country_code": "DE", "state": "Berlin"}}]
    mock_client(monkeypatch, lambda request: httpx.Response(200, json=data if provider is PhotonSource else nominatim))
    source = provider("https://geocoder.test/", "test", 5, min_interval=0)
    records = asyncio.run(source.search(entity("Example Mine")))
    assert len(records) == 1
    assert records[0].name == "Example Mine"
    assert records[0].subtype == provider_type
    assert records[0].geometry.coordinates == (13.388, 52.517)
    assert records[0].geometry_basis == ("representative_point" if provider_type == "mine" else "unknown")
    assert records[0].category == (Category.mining if provider_type == "mine" else Category.other)
    assert records[0].evidence["provider_type"] == provider_type


def test_default_geocoder_routes_places_and_skips_mining_assets(tmp_path):
    from georeferencer.config import Settings
    from georeferencer.pipeline import default_workflows
    from georeferencer.models import TYPOLOGY, VerificationClass
    settings = Settings(geocoder_provider="photon", geocoder_url="https://geocoder.test/",
                        library_dir=tmp_path, model="test", web_search_enabled=True)
    workflows = default_workflows(settings)
    assert [workflow.name for workflow in workflows] == ["geocoder", "explicit_coordinates", "web_search"]
    for category, subtypes in TYPOLOGY.items():
        e = entity()
        e.category, e.subtype = category, next(iter(subtypes))
        assert workflows[0].supports(e) == (category == Category.geographic)
    e = entity()
    e.verification_classes = [VerificationClass.mineral_site]
    assert not workflows[0].supports(e)
    e.category, e.subtype = Category.other, "facility"
    e.verification_classes = [VerificationClass.geographic_place]
    assert workflows[0].supports(e)


@pytest.mark.parametrize("ambiguous", [False, True])
def test_regional_geocoder_query_then_country_fallback_and_ambiguity(tmp_path, monkeypatch, ambiguous):
    import json
    from georeferencer.workflows import GeocoderWorkflow
    from georeferencer.observability import RunContext
    calls = []
    e = entity()
    e.jurisdiction = "Berlin State"
    def handler(request):
        calls.append(request)
        assert request.url.params["limit"] == "20"
        assert request.url.params["countrycode"] == "DE"
        data = response()
        if len(calls) == 1 and not ambiguous:
            data["features"][0]["properties"]["state"] = "Bavaria"
        if ambiguous:
            other = response()["features"][0]
            other["properties"]["osm_id"] = 999
            data["features"].append(other)
        return httpx.Response(200, json=data)
    mock_client(monkeypatch, handler)
    workflow = GeocoderWorkflow("geocoder", PhotonSource("https://photon.test/api", "test", 5, min_interval=0), set(Category))
    ctx = RunContext("query-order", tmp_path, 4, 0)
    result = asyncio.run(workflow.verify(e, ctx))
    assert calls[0].url.params["q"] == "Berlin, Berlin State"
    if ambiguous:
        assert len(calls) == 1 and result.status == "conflicting"
        assert result.geometry is None
    else:
        assert calls[1].url.params["q"] == "Berlin"
        assert result.status == "confirmed" and result.geometry is not None
        assert len(result.candidates) == 2  # Both attempts retained for debugging.
    events = [json.loads(line) for line in ctx.path.read_text().splitlines()]
    requests = [event for event in events if event["event"] == "source.request"]
    assert requests[0]["query_strategy"] == "region_then_country"
    assert requests[0]["refined"] is True
    if not ambiguous:
        assert requests[1]["refined"] is False
        assert requests[1]["query"] == calls[1].url.params["q"]
