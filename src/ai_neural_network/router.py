"""Deterministic worker routing: cheapest healthy capable worker first, with explicit skip reasons."""
from __future__ import annotations

import json
import shutil
import urllib.request
from dataclasses import dataclass

from . import health
from .procs import FleetError
from .registry import Registry, Worker


@dataclass
class Candidate:
    worker: str
    eligible: bool
    reason: str | None = None


def comfy_busy(url: str = "http://127.0.0.1:8000/queue") -> bool:
    try:
        with urllib.request.urlopen(url, timeout=1) as r:
            q = json.loads(r.read())
        return bool(q.get("queue_running") or q.get("queue_pending"))
    except (OSError, ValueError):
        return False


def _order(reg: Registry, capability: str) -> list[str]:
    capable = [w for w in reg.workers.values() if capability in w.capabilities]

    def key(w: Worker) -> tuple:
        cost_idx = reg.cost_order.index(w.cost) if w.cost in reg.cost_order else len(reg.cost_order)
        return (cost_idx, w.rank, w.id)

    derived = [w.id for w in sorted(capable, key=key)]
    chain = [w for w in reg.chains.get(capability, []) if w in reg.workers]
    return chain + [w for w in derived if w not in chain]


def skip_reason(w: Worker, capability: str, *, allow_claude: bool, gpu_busy: bool) -> str | None:
    if not w.enabled:
        return "disabled"
    if w.opt_in and not allow_claude:
        return "opt-in (operator only)"
    if capability not in w.capabilities:
        return f"lacks capability {capability}"
    until = health.cooldown_until(w.id)
    if until:
        return f"cooldown until {health.iso(until)}"
    if w.probe and not shutil.which(w.probe[0]):
        return f"{w.probe[0]} not installed"
    if "gpu" in w.resources and gpu_busy:
        return "GPU busy (ComfyUI queue)"
    return None


def candidates(reg: Registry, capability: str = "code", *, allow_claude: bool = False,
               gpu_busy: bool | None = None) -> list[Candidate]:
    busy = comfy_busy() if gpu_busy is None else gpu_busy
    out = []
    for wid in _order(reg, capability):
        reason = skip_reason(reg.workers[wid], capability, allow_claude=allow_claude, gpu_busy=busy)
        out.append(Candidate(wid, reason is None, reason))
    return out


def choose(reg: Registry, capability: str = "code", **kw) -> str:
    cands = candidates(reg, capability, **kw)
    for c in cands:
        if c.eligible:
            return c.worker
    reasons = "; ".join(f"{c.worker}: {c.reason}" for c in cands) or "no worker has it"
    raise FleetError(f"no eligible worker for {capability}: {reasons}")
