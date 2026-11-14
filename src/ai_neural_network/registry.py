"""Worker registry: agents, LLM tiers, routing chains and board settings, loaded from workers.yaml."""
from __future__ import annotations

from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

import yaml

from .paths import registry_path


@dataclass(frozen=True)
class Worker:
    id: str
    kind: str
    control: str
    cost: str
    capabilities: list[str]
    notes: str = ""
    probe: list[str] | None = None
    enabled: bool = True
    opt_in: bool = False
    acpx_agent: str | None = None
    argv: list[str] | None = None
    timeout_s: int = 1800
    quota_patterns: list[str] = field(default_factory=list)
    cooldown_s: int = 3600
    rank: int = 50
    resources: list[str] = field(default_factory=list)
    max_parallel: int = 2


@dataclass(frozen=True)
class Tier:
    id: str
    base_url: str
    model: str
    cost: str
    notes: str = ""
    key_env: str = ""  # env var holding the bearer key (also read from ~/.secrets/.env)
    session_header: bool = False  # upstream needs x-opencode-session UUID header (opencode.ai direct)
    catalog: str = ""  # models.dev provider id, for `fleet doctor --catalog`
    cli: str = ""  # "agy": answer through one Antigravity print call (Google subscription), not HTTP
    daily_cap: int = 0  # most calls per UTC day (0 = no cap); for subscription tiers shared with people's own use
    effort: str = ""  # reasoning effort passed to codex/claude CLI tiers (agy puts it in the model id)
    fallback: list[str] = field(default_factory=list)  # tiers tried in order when this one fails (named-tier asks only)


@dataclass(frozen=True)
class Registry:
    workers: dict[str, Worker]
    tiers: dict[str, Tier]
    cost_order: list[str]
    chains: dict[str, list[str]]
    board: dict[str, Any]
    throttle: dict[str, dict[str, Any]] = field(default_factory=dict)
    quota_patterns: list[str] = field(default_factory=list)


_WORKER_KEYS = {f.name for f in fields(Worker)} - {"id"}
_TIER_KEYS = {f.name for f in fields(Tier)} - {"id"}
_DEFAULT_KEYS = {"timeout_s", "quota_patterns", "cooldown_s", "rank", "max_parallel"}
_TOP_KEYS = {"defaults", "workers", "tiers", "routing", "board", "throttle"}
_THROTTLE_KEYS = {"min_interval_s", "max_concurrent", "max_queue_s", "slot_ttl_s"}


def _first(keys: set[str]) -> str:
    return sorted(keys)[0]


def load(path: str | Path | None = None) -> Registry:
    source = Path(path) if path else registry_path()
    data = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    if unknown := set(data) - _TOP_KEYS:
        raise ValueError(f"Unknown registry key: {_first(unknown)}")
    defaults = data.get("defaults") or {}
    if unknown := set(defaults) - _DEFAULT_KEYS:
        raise ValueError(f"Unknown defaults key: {_first(unknown)}")
    workers: dict[str, Worker] = {}
    for wid, raw in (data.get("workers") or {}).items():
        if unknown := set(raw) - _WORKER_KEYS:
            raise ValueError(f"Unknown worker key for {wid}: {_first(unknown)}")
        values = {**defaults, **raw}
        for key in ("capabilities", "quota_patterns", "resources"):
            values[key] = list(values.get(key) or [])
        workers[wid] = Worker(id=wid, **values)
    tiers: dict[str, Tier] = {}
    for tid, raw in (data.get("tiers") or {}).items():
        if unknown := set(raw) - _TIER_KEYS:
            raise ValueError(f"Unknown tier key for {tid}: {_first(unknown)}")
        tiers[tid] = Tier(id=tid, **{**raw, "fallback": list(raw.get("fallback") or [])})
    for t in tiers.values():
        if bad := [f for f in t.fallback if f not in tiers or f == t.id]:
            raise ValueError(f"tier {t.id} fallback names unknown or itself: {bad[0]}")
    routing = data.get("routing") or {}
    throttle = data.get("throttle") or {}
    for pkey, pcfg in throttle.items():
        if not isinstance(pcfg, dict) or set(pcfg) - _THROTTLE_KEYS:
            raise ValueError(f"bad throttle config for {pkey}")
    return Registry(workers=workers, tiers=tiers,
                    cost_order=list(routing.get("cost_order") or ["free", "flat", "metered", "claude"]),
                    chains={k: list(v) for k, v in (routing.get("chains") or {}).items()},
                    board=dict(data.get("board") or {}),
                    throttle={k: dict(v) for k, v in throttle.items()},
                    quota_patterns=list(defaults.get("quota_patterns") or []))


def load_registry(path: str | Path | None = None) -> dict[str, Worker]:
    return load(path).workers


def load_tiers(path: str | Path | None = None) -> dict[str, Tier]:
    return load(path).tiers
