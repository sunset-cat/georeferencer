import asyncio
import json
from types import SimpleNamespace

import pytest

from georeferencer.config import Settings
from georeferencer.coordinates import parse_dms_pair
from georeferencer.extraction import parse_extraction
from georeferencer.models import Candidate, Category, CoordinateClaim, Entity, Extraction, Mention
from georeferencer.observability import RunContext
from georeferencer.pipeline import Pipeline
from georeferencer.web_resolution import validate_candidate, WebResolution
from georeferencer.workflows import ExplicitCoordinates, SourceWorkflow

DMS = '10° 45\' 4" south latitude and 48° 4\' 20" west longitude'
LOCATION = f'The Serra Alta deposit is located at {DMS}.'


def entity():
    return Entity(id="serra", name="Serra Alta deposit", category="mining", subtype="mineralized_zone",
                  role="primary_subject", aliases=["Serra Alta"], mentions=[Mention(quote="Serra Alta deposit")],
                  identifiers=[], country_code="BR", jurisdiction="Tocantins")


def claim():
    return dict(url="https://survey.example/report", identity_quote="Serra Alta deposit in Tocantins, Brazil.",
                coordinate_quote=LOCATION, longitude=None, latitude=None, crs="WGS84",
                crs_quote="Geographic coordinates in this report use WGS84.", meaning="representative_point")


@pytest.mark.parametrize("notation", [DMS, '10°45′4″S 48°4′20″W', 'S 10°45\'4" W 48°4\'20"',
                                      '10 degrees 45 minutes 4 seconds south 48 degrees 4 minutes 20 seconds west'])
def test_dms_conversion_is_deterministic_and_preserves_original(notation):
    pair = parse_dms_pair(notation)
    assert pair.latitude == pytest.approx(-10.751111111)
    assert pair.longitude == pytest.approx(-48.072222222)
    assert pair.latitude_expression in notation
    assert pair.longitude_expression in notation


def test_north_east_and_decimal_seconds():
    pair = parse_dms_pair('12°3′4.5″N 45°6′7,5″E')
    assert pair.latitude == pytest.approx(12 + 3 / 60 + 4.5 / 3600)
    assert pair.longitude == pytest.approx(45 + 6 / 60 + 7.5 / 3600)


def test_degrees_decimal_minutes_and_double_primes():
    pair = parse_dms_pair("10°45.5′S 48°4.25′W")
    assert pair.latitude == pytest.approx(-(10 + 45.5 / 60))
    assert pair.longitude == pytest.approx(-(48 + 4.25 / 60))
    assert parse_dms_pair("10°45′4′′S 48°4′20′′W").longitude == pytest.approx(-48.072222222)


@pytest.mark.parametrize("text", [
    '10°60\'4"S 48°4\'20"W', '10°45\'60"S 48°4\'20"W',
    '91°0\'0"S 48°4\'20"W', '10°45\'4"S 180°0\'1"W',
    '-10°45\'4"N 48°4\'20"W', '+10°45\'4"S 48°4\'20"W',
    '10°45\'4" 48°4\'20"W', '10°45\'4"S',
    '10°45\'4"S 48°4\'20"W 11°45\'4"S',
    'N 10°45\'4"S 48°4\'20"W',
])
def test_invalid_incomplete_or_ambiguous_dms_is_not_guessed(text):
    with pytest.raises(ValueError):
        parse_dms_pair(text)


def test_web_accepts_retrieved_dms_with_datum_evidence_elsewhere():
    c = claim()
    text = c["identity_quote"] + " " + LOCATION + " " + c["crs_quote"]
    candidate = validate_candidate(entity(), c, text)
    assert candidate.geometry.coordinates == pytest.approx((-48.072222222, -10.751111111))
    assert candidate.evidence["coordinate_format"] == "dms"
    assert candidate.evidence["coordinate_quote"] == LOCATION
    assert candidate.evidence["crs_quote"] == c["crs_quote"]
    assert not candidate.independence == "independent"
    # The model may omit a CRS field even when its sourced quotation establishes it.
    c["crs"] = None
    assert validate_candidate(entity(), c, text).geometry == candidate.geometry


def test_web_dms_must_be_verbatim_and_belong_to_requested_entity():
    c = claim()
    with pytest.raises(ValueError, match="absent"):
        validate_candidate(entity(), c, c["identity_quote"] + c["crs_quote"])
    c["coordinate_quote"] = LOCATION.replace("Serra Alta deposit", "Another deposit")
    with pytest.raises(ValueError, match="requested entity"):
        validate_candidate(entity(), c, c["identity_quote"] + c["coordinate_quote"] + c["crs_quote"])
    c = claim()
    c["longitude"] = -40
    with pytest.raises(ValueError, match="disagree"):
        validate_candidate(entity(), c, c["identity_quote"] + LOCATION + c["crs_quote"])


def test_unspecified_source_datum_uses_labelled_wgs84_fallback():
    c = claim()
    c.update(crs=None, crs_quote=None)
    candidate = validate_candidate(entity(), c, c["identity_quote"] + LOCATION)
    assert candidate.geometry.coordinates == pytest.approx((-48.072222222, -10.751111111))
    assert float(candidate.evidence["converted_longitude"]) == pytest.approx(-48.072222222)
    assert candidate.evidence["source_crs"] == "unknown"
    assert candidate.evidence["crs_status"] == "assumed"
    assert "WGS84 assumed" in candidate.evidence["coordinate_validation"]


def test_null_quote_does_not_verify_claim_in_llm_note():
    c = claim()
    c.update(coordinate_quote=None)
    candidate = validate_candidate(entity(), c, c["identity_quote"])
    assert candidate.geometry is None
    assert "No coordinate quotation" in candidate.evidence["coordinate_validation"]


def test_document_dms_claim_is_converted_anchored_and_exported(tmp_path):
    quote = LOCATION + " WGS84."
    e = entity()
    payload = e.model_dump(mode="json")
    payload.pop("category")
    payload["mentions"] = ["Serra Alta deposit"]
    payload["coordinate_claims"] = [{"longitude": None, "latitude": None, "coordinate_format": "dms",
                                    "crs": "WGS84", "meaning": "representative_point", "evidence": {"quote": quote}}]
    extracted = parse_extraction(json.dumps({"entities": [payload]}))
    class Extractor:
        async def extract(self, document, ctx, feedback):
            return extracted
    result = asyncio.run(Pipeline(Settings(log_dir=tmp_path), Extractor(), [ExplicitCoordinates()]).run("DMS document", quote))
    assert result.status == "completed" and not result.issues
    assert result.geojson["features"][0]["geometry"]["coordinates"] == pytest.approx((-48.072222222, -10.751111111))
    assert result.entities[0].geometry_provenance.origin == "document"
    assert result.entities[0].entity.coordinate_claims[0].evidence.start == 0
    assert result.entities[0].entity.coordinate_claims[0].coordinate_format == "dms"


def test_document_dms_unknown_crs_is_accepted_without_changing_source_claim(tmp_path):
    e = entity()
    e.coordinate_claims = [CoordinateClaim(coordinate_format="dms", crs="unknown", meaning="representative_point", evidence=Mention(quote=LOCATION))]
    check = asyncio.run(ExplicitCoordinates().verify(e, RunContext("datum", tmp_path, 0, 0)))
    assert check.geometry.coordinates == pytest.approx((-48.072222222, -10.751111111))
    assert e.coordinate_claims[0].latitude == pytest.approx(-10.751111111)
    assert e.coordinate_claims[0].crs == "unknown"
    assert not check.issues


@pytest.mark.parametrize("datum", ["SAD69", "SAD 69", "EPSG:4618"])
def test_explicit_other_datum_never_uses_default(datum):
    c = claim()
    c.update(crs=datum, crs_quote=f"Coordinates use {datum}.")
    with pytest.raises(ValueError, match="CRS"):
        validate_candidate(entity(), c, c["identity_quote"] + LOCATION + c["crs_quote"])


def test_decimal_unspecified_datum_is_accepted_and_labelled():
    c = claim()
    c.update(coordinate_quote="Serra Alta deposit longitude -48.07 latitude -10.75.",
             longitude=-48.07, latitude=-10.75, crs=None, crs_quote=None)
    candidate = validate_candidate(entity(), c, c["identity_quote"] + c["coordinate_quote"])
    assert candidate.geometry.coordinates == (-48.07, -10.75)
    assert candidate.evidence["crs_status"] == "assumed"


def test_web_workflow_saves_and_replays_converted_source(tmp_path, monkeypatch):
    c = claim()
    usage = SimpleNamespace(input_tokens=10, output_tokens=20, total_tokens=30,
                            model_dump=lambda: {"input_tokens": 10, "output_tokens": 20})
    item = SimpleNamespace(model_dump=lambda **kwargs: {"type": "web_search_call", "status": "completed",
                          "action": {"type": "search", "sources": [{"url": c["url"]}]}})
    class Client:
        async def __aenter__(self):
            self.responses = self
            return self
        async def __aexit__(self, *args):
            pass
        async def create(self, **kwargs):
            return SimpleNamespace(output=[item], usage=usage, id="dms-response", status="completed",
                                   output_text=json.dumps({"candidates": [c]}))
    async def download(*args, **kwargs):
        return (c["identity_quote"] + LOCATION + c["crs_quote"]).encode(), "text/plain", c["url"]
    monkeypatch.setattr("georeferencer.web_resolution.AsyncOpenAI", lambda **kwargs: Client())
    monkeypatch.setattr("georeferencer.web_resolution.download_document", download)
    workflow = WebResolution(Settings(model="gpt-6.1-sol", library_dir=tmp_path, source_document_cache_dir=tmp_path / "sources"))
    first = RunContext("first", tmp_path, 6, 2)
    result = asyncio.run(workflow.verify(entity(), first))
    assert result.status == "supported"
    assert result.geometry.coordinates == pytest.approx((-48.072222222, -10.751111111))
    replay = RunContext("second", tmp_path, 0, 0)
    assert asyncio.run(workflow.verify(entity(), replay)).geometry == result.geometry
    assert replay.usage.model_calls == 0
    assert replay.raw_llm_responses[0]["served_from_cache"]


def test_refinement_keeps_initial_rejections_and_records_actual_query(tmp_path):
    wrong = Candidate(name="Serra Alta", category=Category.geographic, subtype="settlement", country_code="BR",
                      jurisdiction="Santa Catarina", source="photon", source_record_id="1")
    class Source:
        name = "photon"
        supports_refinement = True
        def query_text(self, e, refined=False):
            return e.name + (", " + e.jurisdiction if refined else "")
        async def search(self, e, refined=False):
            return [] if refined else [wrong]
    ctx = RunContext("geocoder", tmp_path, 5, 0)
    result = asyncio.run(SourceWorkflow("geocoder", Source(), set(Category)).verify(entity(), ctx))
    assert result.status == "no_match" and result.candidates == [wrong]
    reasons = result.candidate_assessments[0]["reasons"]
    assert len(reasons) == 2
    assert any("Santa Catarina" in reason for reason in reasons)
    assert any("Type differs" in reason for reason in reasons)
    logs = [json.loads(line) for line in ctx.path.read_text().splitlines()]
    requests = [event for event in logs if event["event"] == "source.request"]
    assert [r["query"] for r in requests] == ["Serra Alta deposit, Tocantins", "Serra Alta deposit"]
    assert requests[0]["refined"] and not requests[1]["refined"]
    assert "country only" in requests[1]["query_reason"]
