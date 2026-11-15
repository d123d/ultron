"""Cost neuromodulation: what today's metered asks cost, and how that scales learning.

Rates are rough public list prices per 1M tokens (in/out) by models.dev catalog;
free/flat/subscription tiers cost 0 here (their quota is handled by cooldowns/caps).
Estimates, not billing: ledger rows carry token counts when the provider sent usage.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from . import ledger
from .registry import Registry

RATE_USD_PER_1M: dict[str, tuple[float, float]] = {
    "openai": (2.0, 15.0), "anthropic": (3.0, 15.0), "azure": (2.0, 15.0),
    "google": (0.5, 3.0), "google-vertex": (0.5, 3.0), "amazon-bedrock": (1.0, 5.0),
    "mistral": (2.0, 6.0), "cohere": (1.0, 4.0), "xai": (2.0, 10.0),
    "perplexity": (1.0, 5.0), "moonshotai": (0.6, 2.5), "minimax": (0.3, 1.2),
}
DEFAULT_RATE = (0.5, 1.5)  # open-model clouds (groq/together/fireworks/...)
DEFAULT_DAILY_BUDGET_USD = 5.0


def estimate_usd(reg: Registry, row: dict[str, Any]) -> float:
    tier = reg.tiers.get(row.get("tier") or "")
    if tier is None or tier.cost != "metered":
        return 0.0
    rin, rout = RATE_USD_PER_1M.get(tier.catalog, DEFAULT_RATE)
    return ((row.get("input_tokens") or 0) * rin + (row.get("output_tokens") or 0) * rout) / 1_000_000


def spend_today(reg: Registry, rows: list[dict[str, Any]] | None = None) -> dict[str, float]:
    day = datetime.now(UTC).date().isoformat()
    per: dict[str, float] = {}
    for r in (ledger.read() if rows is None else rows):
        if r.get("type") == "ask" and str(r.get("ts", "")).startswith(day):
            usd = estimate_usd(reg, r)
            if usd:
                per[r["tier"]] = round(per.get(r["tier"], 0.0) + usd, 6)
    return per


def modulation(reg: Registry, budget_usd: float = DEFAULT_DAILY_BUDGET_USD,
               rows: list[dict[str, Any]] | None = None) -> float:
    """Learning-rate scalar from today's metered spend: 1.0 fresh, floor 0.2 at/over budget.

    Success on a nearly-spent day teaches the network less — expensive pathways
    stop entrenching themselves once the money is gone for the day.
    """
    spent = sum(spend_today(reg, rows).values())
    if budget_usd <= 0:
        return 1.0
    return max(0.2, 1.0 - 0.8 * min(1.0, spent / budget_usd))
