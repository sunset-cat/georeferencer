import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from georeferencer.api import create_app
from georeferencer.config import Settings
from georeferencer.extraction import FixtureExtractor
from georeferencer.models import Usage
from georeferencer.pipeline import Pipeline
from georeferencer.pricing import estimate_cost, with_cost


def response(model="gpt-6.1-sol", inputs=1000, outputs=100, reads=200, writes=300, **kwargs):
    return {"model": model, "served_from_cache": False,
            "usage": {"input_tokens": inputs, "output_tokens": outputs,
                      "input_tokens_details": {"cached_tokens": reads, "cache_write_tokens": writes}}, **kwargs}


def test_disjoint_cache_buckets_and_reasoning_output_are_priced_once():
    cost = estimate_cost(Usage(model_calls=1), [response()])
    # 500 ordinary, 200 cache reads, 300 cache writes, 100 total output.
    assert cost.status == "estimated"
    assert cost.total_usd == pytest.approx(0.00277)
    assert cost.calls[0]["tokens"] == {"input": 500, "cached_input": 200, "cache_write": 300, "output": 100}


def test_local_cache_replays_do_not_charge_historical_tokens_or_searches():
    old = response(model="unknown", served_from_cache=True, stage="web_search",
                   tool_calls=[{"action": {"type": "search"}, "status": "completed"}])
    free = estimate_cost(Usage(), [old])
    assert free.total_usd == 0
    assert free.status == "estimated"
    assert free.local_cache_hits == 1
    assert not free.calls


def test_all_fresh_calls_including_corrections_use_their_own_model_price():
    records = [response("gpt-5.4-mini", reads=0, writes=0),
               response("gpt-6-astra-2026-08-01", reads=0, writes=0, correction=True),
               response(served_from_cache=True)]
    cost = estimate_cost(Usage(model_calls=2), records)
    assert cost.status == "estimated"
    assert cost.total_usd == pytest.approx(0.0012 + 0.015)
    assert cost.local_cache_hits == 1


def test_only_completed_search_actions_incur_web_fee_not_page_opens_or_sources():
    record = response(stage="web_search", tool_calls=[
        {"action": {"type": "search", "queries": ["one", "two"]}, "status": "completed"},
        {"action": {"type": "search"}, "status": "completed"},
        {"action": {"type": "open_page"}, "status": "searching"},
        {"action": {"type": "find_in_page"}, "status": "completed"}])
    cost = estimate_cost(Usage(model_calls=1, web_search_tool_calls=4), [record])
    assert cost.status == "estimated"
    assert cost.web_search_cost_usd == 0.02
    assert cost.total_usd == pytest.approx(0.02277)


@pytest.mark.parametrize("record,reason", [
    (response("unlisted-model"), "No verified price"),
    (response(service_tier="priority"), "tier price"),
    (response(usage=None), "Token usage missing"),
    (response(reads=800, writes=800), "exceed input"),
])
def test_unavailable_prices_or_usage_do_not_display_free_runs(record, reason):
    cost = estimate_cost(Usage(model_calls=1), [record])
    assert cost.status == "unavailable"
    assert cost.total_usd is None
    assert any(reason in note for note in cost.notes)


def test_partial_cost_retains_known_calls_and_reports_missing_failed_call():
    cost = estimate_cost(Usage(model_calls=2, model_calls_without_usage=1), [response()])
    assert cost.status == "incomplete"
    assert cost.total_usd == pytest.approx(0.00277)
    assert len(cost.notes) == 2


def test_missing_legacy_raw_records_cannot_be_priced_from_current_model():
    cost = with_cost({"usage": {"model_calls": 1, "input_tokens": 1000, "output_tokens": 100}})["estimated_cost"]
    assert cost["status"] == "unavailable"
    assert cost["total_usd"] is None


def test_search_fees_can_be_known_when_token_usage_is_unavailable():
    record = response(usage=None, stage="web_search",
                      tool_calls=[{"action": {"type": "search"}, "status": "completed"}])
    cost = estimate_cost(Usage(model_calls=1, model_calls_without_usage=1), [record])
    assert cost.status == "incomplete"
    assert cost.total_usd == 0.01


def test_incomplete_search_metadata_is_not_silently_free():
    record = response(stage="web_search", tool_calls=[{"status": "completed"}])
    cost = estimate_cost(Usage(model_calls=1), [record])
    assert cost.status == "incomplete"
    assert any("billing details" in note for note in cost.notes)


def test_long_context_multiplier_applies_to_entire_request_at_threshold():
    short = estimate_cost(Usage(model_calls=1), [response(inputs=272000, reads=0, writes=0)])
    long = estimate_cost(Usage(model_calls=1), [response(inputs=272001, reads=0, writes=0)])
    assert short.total_usd == pytest.approx(0.545)
    assert long.total_usd == pytest.approx(1.089504)
    assert long.calls[0]["long_context"]


def test_estimate_is_persisted_logged_and_available_for_legacy_runs_without_rerun(tmp_path):
    directory = Path(__file__).resolve().parents[1] / "fixtures"
    engine = Pipeline(Settings(model="gpt-6-astra", library_dir=tmp_path / "library", log_dir=tmp_path / "logs"),
                      FixtureExtractor(directory / "demo.extraction.json"), workflows=[])
    application = create_app(engine)
    store = application.state.library.store
    identifier = store.create("fixture.txt", content=(directory / "demo.txt").read_bytes())
    result = asyncio.run(engine.run("fixture", (directory / "demo.txt").read_text()))
    assert result.estimated_cost.total_usd == 0
    events = [json.loads(line) for line in (engine.settings.log_dir / f"{result.request_id}.jsonl").read_text().splitlines()]
    assert events[-1]["estimated_cost"]["total_usd"] == 0
    store.finish(identifier, result)
    assert json.loads(store.get(identifier)["result"])["estimated_cost"]["total_usd"] == 0
    # Emulate an old mini report while current configuration is Astra.
    legacy = result.model_dump(mode="json")
    legacy.pop("estimated_cost")
    legacy["usage"]["model_calls"] = 1
    legacy["raw_llm_responses"] = [response("gpt-5.4-mini", reads=0, writes=0)]
    original = json.dumps(legacy)
    with store.connect() as db:
        db.execute("UPDATE documents SET result=? WHERE id=?", (original, identifier))
        db.execute("UPDATE runs SET result=? WHERE request_id=?", (original, result.request_id))
    with TestClient(application) as client:
        detail = client.get(f"/library/{identifier}").json()["result"]
        history = client.get(f"/library/{identifier}/runs").json()[0]["result"]
        assert detail["estimated_cost"]["total_usd"] == pytest.approx(0.0012)
        assert history["estimated_cost"] == detail["estimated_cost"]
    assert store.get(identifier)["result"] == original
