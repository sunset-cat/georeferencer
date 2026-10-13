"""Versioned public USD rates; estimate recorded API usage, never cached replays.

Search content is already included in response input usage. Only search actions
incur the extra web fee; opening pages and URLs returned are not search calls.
This is a list-price estimate, not an account invoice (taxes/discounts excluded).
"""
import re
from dataclasses import dataclass
from decimal import Decimal

from .models import CostEstimate, Usage

PRICING_AS_OF = "2026-10-10"
PRICING_SOURCE = "https://developers.openai.com/api/docs/pricing"
WEB_SEARCH_USD = Decimal("0.01")
MILLION = Decimal(1_000_000)


@dataclass(frozen=True)
class Rates:
    input: str
    cached_input: str
    cache_write: str
    output: str
    long_context_threshold: int | None = None


# Standard rates per million tokens, verified against official model pages.
# Add verified models here; unknown models must not silently borrow a price.
MODEL_RATES = {
    "gpt-6-luna": Rates("0.10", "0.01", "0.125", "0.50", 272_000),
    "gpt-6.1-sol": Rates("2", "0.10", "2.50", "10", 272_000),
    "gpt-6-sol": Rates("2", "0.20", "2.50", "10", 272_000),
    "gpt-6-astra": Rates("10", "1", "12.50", "50", 272_000),
    "gpt-5.4-mini": Rates("0.75", "0.075", "0.75", "4.50"),
}


def estimate_cost(usage: Usage, responses: list[dict]) -> CostEstimate:
    notes = []
    calls = []
    token_total = Decimal(0)
    search_total = Decimal(0)
    priced_calls = 0
    cached = sum(bool(r.get("served_from_cache")) for r in responses)
    fresh = [r for r in responses if not r.get("served_from_cache")]
    for response in fresh:
        model = response.get("model") or "unknown"
        # Accept dated snapshots of known models, not arbitrary prefix matches.
        base = re.sub(r"-\d{4}-\d{2}-\d{2}$", "", model)
        rates = MODEL_RATES.get(base)
        tier = response.get("service_tier") or "default"
        searches = 0
        for tool in response.get("tool_calls", []):
            action = (tool.get("action") or {}).get("type")
            if action == "search" and tool.get("status") == "completed":
                searches += 1
            elif action not in {"open_page", "find_in_page"}:
                notes.append("Some web actions have missing or incomplete billing details; their fees are not included.")
        web_cost = WEB_SEARCH_USD * searches
        search_total += web_cost
        call = {"model": model, "stage": response.get("stage", "extraction"),
                "correction": bool(response.get("correction")),
                "service_tier": tier, "web_search_calls": searches,
                "web_search_cost_usd": float(web_cost), "token_cost_usd": None}
        calls.append(call)
        recorded = response.get("usage") or {}
        if response.get("stage") == "web_search" and "tool_calls" not in response:
            notes.append("Web action records are missing; search fees cannot be estimated for every call.")
        if not rates:
            notes.append(f"No verified price configured for {model}; its tokens are not priced.")
            continue
        if tier not in {"default", "standard", "auto"}:
            notes.append(f"No verified {tier} tier price configured for {model}; its tokens are not priced.")
            continue
        details = recorded.get("input_tokens_details") or {}
        counts = [recorded.get("input_tokens"), recorded.get("output_tokens"),
                  details.get("cached_tokens", 0), details.get("cache_write_tokens", 0)]
        if any(type(n) is not int or n < 0 for n in counts):
            notes.append(f"Token usage missing or invalid for a {model} call; its tokens are not priced.")
            continue
        inputs, outputs, reads, writes = counts
        ordinary = inputs - reads - writes
        if ordinary < 0:
            notes.append(f"Token cache counts exceed input usage for {model}; its tokens are not priced.")
            continue
        long_context = bool(rates.long_context_threshold and inputs > rates.long_context_threshold)
        input_multiplier = 2 if long_context else 1
        output_multiplier = Decimal("1.5") if long_context else Decimal(1)
        buckets = {"input": ordinary, "cached_input": reads, "cache_write": writes, "output": outputs}
        effective_rates = {name: Decimal(getattr(rates, name)) *
                           (output_multiplier if name == "output" else input_multiplier) for name in buckets}
        cost = sum(Decimal(count) * effective_rates[name] / MILLION for name, count in buckets.items())
        token_total += cost
        priced_calls += 1
        call.update(tokens=buckets, rates_per_million_usd={k: float(v) for k, v in effective_rates.items()},
                    long_context=long_context, token_cost_usd=float(cost), total_usd=float(cost + web_cost))
    if len(fresh) != usage.model_calls:
        notes.append("Recorded model calls and saved responses differ; some call costs are unavailable.")
    if usage.model_calls_without_usage:
        notes.append("Some API calls returned no token usage; their possible charges are unknown.")
    notes = list(dict.fromkeys(notes))
    complete = not notes
    available = priced_calls > 0 or search_total > 0 or (usage.model_calls == 0 and not fresh)
    return CostEstimate(status="estimated" if complete else "incomplete" if available else "unavailable",
        total_usd=float(token_total + search_total) if available else None,
        token_cost_usd=float(token_total), web_search_cost_usd=float(search_total),
        pricing_as_of=PRICING_AS_OF, pricing_source=PRICING_SOURCE,
        local_cache_hits=cached, calls=calls, notes=notes)


def with_cost(result: dict) -> dict:
    """Expose older saved runs without rewriting history or making model calls."""
    if result.get("estimated_cost") is None:
        result["estimated_cost"] = estimate_cost(Usage.model_validate(result.get("usage", {})),
                                               result.get("raw_llm_responses", [])).model_dump(mode="json")
    return result
