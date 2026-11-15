"""Provider slots, job slots and the background daemon.

Provider slot: one entry per upstream provider (every tier that shares an endpoint/login), with its
concurrency and the list of models it serves (configured tiers + the provider's live /models list).
Job slot: a kind of work (config/jobs.yaml) mapped to an ordered list of models, tried in order.
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from . import health, ledger, registry
from .llm import _secret_or_none, ask, vote
from .paths import config_dir, state_dir
from .procs import FleetError
from .registry import Registry, provider_name
from .throttle import config_for

_UA = "Mozilla/5.0 ultron"  # opencode.ai (Cloudflare) rejects urllib's default UA


def models_path() -> Path:
    return config_dir() / "provider_models.json"


def _live_models() -> dict[str, Any]:
    try:
        return json.loads(models_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def provider_slots(reg: Registry) -> list[dict[str, Any]]:
    """One row per provider: its tiers, concurrency, key state and every model it can serve."""
    live = _live_models()
    slots: dict[str, dict[str, Any]] = {}
    for t in reg.tiers.values():
        name = provider_name(t)
        s = slots.setdefault(name, {"provider": name, "base_url": t.base_url, "via": t.cli or t.adapter or "http",
                                    "cost": t.cost, "key_env": t.key_env or "-",
                                    "key_present": None if not t.key_env else bool(_secret_or_none(t.key_env)),
                                    "slots": int(config_for(reg, t.base_url)["max_concurrent"]),
                                    "tiers": [], "models": []})
        s["tiers"].append(t.id)
        if t.model not in s["models"]:
            s["models"].append(t.model)
    for name, s in slots.items():
        s["models"] += [m for m in (live.get(name) or {}).get("models", []) if m not in s["models"]]
        s["n_models"] = len(s["models"])
        s["cooled"] = [t for t in s["tiers"] if health.in_cooldown(t)]
    return list(slots.values())


def harness_slots(reg: Registry) -> list[dict[str, Any]]:
    """One row per local harness (agent worker): how it is driven, its parallel slots, and whether it can run now."""
    import shutil

    rows = []
    for w in reg.workers.values():
        until = health.cooldown_until(w.id)
        installed = bool(shutil.which(w.probe[0])) if w.probe else True
        state = ("disabled" if not w.enabled else "not installed" if not installed else
                 f"cooldown until {health.iso(until)}" if until else "opt-in" if w.opt_in else "ready")
        rows.append({"harness": w.id, "via": w.control, "cost": w.cost, "slots": w.max_parallel, "state": state,
                     "jobs": ",".join(w.capabilities)})
    return rows


def refresh_models(reg: Registry, timeout_s: float = 20) -> dict[str, Any]:
    """Fetch each HTTP provider's /models list and save it to config/provider_models.json."""
    out, seen = _live_models(), set()
    for t in reg.tiers.values():
        name = provider_name(t)
        if name in seen or t.cli or t.adapter:
            continue
        key = _secret_or_none(t.key_env) if t.key_env else "sk-local"
        if not key:
            continue
        seen.add(name)
        req = urllib.request.Request(f"{t.base_url.rstrip('/')}/models",
                                     headers={"Authorization": f"Bearer {key}", "User-Agent": _UA})
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as r:
                data = json.loads(r.read())
            rows = data.get("data") if isinstance(data, dict) else data
            ids = sorted({m["id"] for m in rows or [] if isinstance(m, dict) and m.get("id")})
        except (OSError, ValueError, KeyError, TypeError) as e:
            out.setdefault(name, {})["error"] = str(e)[:120]
            continue
        if ids:
            out[name] = {"fetched": datetime.now(UTC).date().isoformat(), "models": ids}
    models_path().write_text(json.dumps(out, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return {k: len(v.get("models", [])) for k, v in out.items()}


def load_jobs() -> dict[str, dict[str, Any]]:
    return (yaml.safe_load((config_dir() / "jobs.yaml").read_text(encoding="utf-8")) or {}).get("jobs") or {}


def check_jobs(reg: Registry) -> list[str]:
    """Config errors: a job naming a tier/provider/capability the registry does not have."""
    from .llm import candidates

    bad = []
    for kind, job in load_jobs().items():
        for spec in job.get("tiers") or []:
            try:
                candidates(reg, spec + ",")
            except FleetError as e:
                bad.append(f"{kind}: {e}")
        if job.get("mode") == "run" and not any(job.get("capability") in w.capabilities for w in reg.workers.values()):
            bad.append(f"{kind}: no worker has capability {job.get('capability')!r}")
    return bad


def run_job(reg: Registry, kind: str, prompt: str, *, cwd: str | None = None, done_when: list[str] | None = None,
            system: str | None = None, images: list[str] | tuple = (), files: list[str] | tuple = (),
            timeout_s: int | None = None, allow_claude: bool = False) -> dict[str, Any]:
    """Do one piece of work in a job slot: the slot's models in order (ask/vote) or its worker chain (run)."""
    jobs = load_jobs()
    if kind not in jobs:
        raise FleetError(f"unknown job {kind!r}; known: {', '.join(jobs)}")
    job = jobs[kind]
    mode = job.get("mode", "ask")
    system = system or job.get("system")
    timeout_s = timeout_s or job.get("timeout_s") or 180
    start = time.monotonic()

    def done(status: str, by: str, **extra) -> dict[str, Any]:
        ledger.append({"type": "job", "job": kind, "tier": by, "status": status,
                       "duration_s": round(time.monotonic() - start, 2)})
        return {"job": kind, "mode": mode, "status": status, "by": by, **extra}

    if mode == "run":
        from .card import TaskCard
        from .runner import RunOptions, run_card

        card = TaskCard(goal=prompt, cwd=cwd or "", capability=job["capability"], done_when=list(done_when or []))
        env = run_card(reg, card, job.get("worker", "auto"), RunOptions(allow_claude=allow_claude, timeout_s=timeout_s))
        return done(env.status, env.worker, text=env.summary, reason=env.reason, files_changed=env.files_changed,
                    log=str(env.log_dir))
    if mode == "vote":
        r = vote(reg, ",".join(job["tiers"]), prompt, system=system, files=files, timeout_s=timeout_s)
        return done("ok", r.tier, text=r.data["answer"], votes=r.data["votes"])
    errors = []
    if job.get("private"):
        r = ask(reg, "local", prompt, system=system, files=files, timeout_s=timeout_s, private=True)
        return done("ok", r.tier, text=r.text, model=r.model)
    for spec in job["tiers"]:
        try:  # "spec," = this model alone: the job's own list is the fallback order
            r = ask(reg, spec + ",", prompt, system=system, files=files, images=images, timeout_s=timeout_s)
            return done("ok", r.tier, text=r.text, model=r.model, duration_s=r.duration_s, skipped=errors)
        except FleetError as e:
            errors.append(f"{spec}: {str(e)[:140]}")
    done("error", "-")
    raise FleetError(f"job {kind}: every model failed: " + " | ".join(errors))


def _pid_alive(pid: int) -> bool:
    import subprocess
    import sys

    if sys.platform == "win32":  # os.kill(pid, 0) terminates the process on Windows
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def reclaim_stale(board) -> list[str]:
    """Reopen cards a dead worker loop on this machine still holds (reboot or restart mid-run left them stuck)."""
    import re
    import socket

    mine = re.compile(rf"fleet-{re.escape(socket.gethostname())}-(\d+)$")
    freed = []
    for issue in board.list("in_progress"):
        m = mine.match(issue.get("assignee") or "")
        if m and not _pid_alive(int(m.group(1))):
            board.reopen(issue["id"], "ultron-daemon", "worker loop died mid-run; reopened")
            freed.append(issue["id"])
    return freed


def daemon(interval_s: int = 60, once: bool = False) -> None:
    """Background loop started at Windows logon: drain the task board, keep a heartbeat, consolidate daily."""
    from .board import Board
    from .network import Network
    from .work import work

    hb = state_dir() / "daemon.json"
    hb.parent.mkdir(parents=True, exist_ok=True)
    processed = 0
    try:  # survive restarts: one consolidation a day, not one per daemon start
        last_sleep = json.loads(hb.read_text(encoding="utf-8")).get("last_sleep", "")
    except (OSError, ValueError):
        last_sleep = ""
    def beat(note: str, state: str) -> None:
        hb.write_text(json.dumps({"pid": os.getpid(), "ts": datetime.now(UTC).isoformat(timespec="seconds"),
                                  "state": state, "processed": processed, "last_error": note,
                                  "last_sleep": last_sleep}), encoding="utf-8")

    while True:
        note = ""
        try:
            reg = registry.load()  # reload: config edits apply without a restart
            beat("", "working")  # a card can run an hour: without this the heartbeat looked dead meanwhile
            board = Board(reg)
            reclaim_stale(board)
            processed += len(work(reg, board, max_tasks=1))
            today = datetime.now().date().isoformat()
            if last_sleep != today:
                Network.from_config().sleep()
                last_sleep = today
        except Exception as e:  # noqa: BLE001 - the daemon must outlive one bad card or a missing board
            note = f"{type(e).__name__}: {e}"[:300]
        beat(note, "idle")
        if once:
            return
        time.sleep(interval_s)
