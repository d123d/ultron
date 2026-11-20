"""Append-only usage ledger (one JSON line per run/ask) and summaries."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from .health import iso, now
from .paths import ledger_path


def append(entry: dict[str, Any]) -> None:
    path = ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps({"ts": iso(now()), **entry}, ensure_ascii=False, sort_keys=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def read(tail: int | None = None) -> list[dict[str, Any]]:
    path = ledger_path()
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows[-tail:] if tail else rows


def summary(rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    rows = read() if rows is None else rows
    per: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"runs": 0, "ok": 0, "failed": 0, "duration_s": 0.0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
    )
    by_cost: dict[str, int] = defaultdict(int)
    for r in rows:
        key = f"{r.get('type', 'run')}:{r.get('worker') or r.get('tier') or '?'}"
        p = per[key]
        p["runs"] += 1
        if r.get("status") == "ok":
            p["ok"] += 1
        else:
            p["failed"] += 1
        p["duration_s"] = round(p["duration_s"] + float(r.get("duration_s") or 0), 2)
        p["input_tokens"] += int(r.get("input_tokens") or 0)
        p["output_tokens"] += int(r.get("output_tokens") or 0)
        p["cost_usd"] = round(p["cost_usd"] + float(r.get("cost_usd") or 0), 6)
        by_cost[r.get("cost") or "?"] += 1
    for p in per.values():
        p["success_rate"] = round(p["ok"] / p["runs"], 3) if p["runs"] else None
    total = sum(by_cost.values())
    share = {k: round(v / total, 3) for k, v in by_cost.items()} if total else {}
    return {"total": total, "per_worker": dict(per), "share_by_cost": share}


QUOTA_WORDS = ("429", "402", "quota", "usage limit", "rate limit", "UsageLimit", "too many requests")


def usage(rows: list[dict[str, Any]] | None = None, at: datetime | None = None) -> dict[str, dict[str, int]]:
    """Calls per worker/tier today (UTC), last 7 and 30 days, plus failures and quota hits in the last 30 days."""
    rows = read() if rows is None else rows
    at = at or now()
    day0 = at.replace(hour=0, minute=0, second=0, microsecond=0)
    out: dict[str, dict[str, int]] = defaultdict(lambda: {"today": 0, "7d": 0, "30d": 0, "failed_30d": 0, "quota_30d": 0})
    for r in rows:
        try:
            ts = datetime.fromisoformat(str(r.get("ts")))
        except ValueError:
            continue
        if ts < at - timedelta(days=30):
            continue
        u = out[f"{r.get('type', 'run')}:{r.get('worker') or r.get('tier') or '?'}"]
        u["30d"] += 1
        u["7d"] += ts >= at - timedelta(days=7)
        u["today"] += ts >= day0
        if r.get("status") != "ok":
            u["failed_30d"] += 1
            u["quota_30d"] += any(w.lower() in str(r.get("error", "")).lower() for w in QUOTA_WORDS)
    return dict(sorted(out.items(), key=lambda kv: -kv[1]["30d"]))
