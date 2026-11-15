"""Live health table for every worker and tier."""
from __future__ import annotations

import json
import shutil
import socket
import subprocess
import time
import urllib.request
from urllib.parse import urlparse

from . import health
from .adapters import acpx_entry
from .llm import _tier_key, ask, calls_today
from .procs import FleetError
from .registry import Registry


def _probe(argv: list[str]) -> tuple[bool, str]:
    exe = shutil.which(argv[0])
    if not exe:
        return False, "not installed"
    try:
        p = subprocess.run([exe, *argv[1:]], capture_output=True, text=True, timeout=20,
                           shell=exe.lower().endswith((".cmd", ".bat")))
        line = (p.stdout or p.stderr).strip().splitlines()
        return p.returncode == 0, line[0][:80] if line else f"exit {p.returncode}"
    except (OSError, subprocess.SubprocessError) as e:
        return False, str(e)[:80]


def _tcp(url: str) -> bool:
    u = urlparse(url)
    port = u.port or (443 if u.scheme == "https" else 80)
    try:
        with socket.create_connection((u.hostname, port), timeout=2):
            return True
    except OSError:
        return False


CATALOG_URL = "https://models.dev/api.json"
OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"


def _get_json(url: str) -> dict:
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "ai-fleet"}), timeout=30) as r:
        return json.loads(r.read())


def catalog_notes(reg: Registry, fetch=_get_json) -> dict[str, str]:
    """Per tier: model gone from its models.dev provider, no structured output, or an OpenRouter expiry date."""
    try:
        cat = fetch(CATALOG_URL)
    except (OSError, ValueError) as e:
        return {t.id: f"catalog unavailable: {e}"[:80] for t in reg.tiers.values() if t.catalog}
    expiry: dict[str, str | None] = {}
    if any("openrouter.ai" in t.base_url for t in reg.tiers.values()):
        try:
            expiry = {m["id"]: m.get("expiration_date") for m in fetch(OPENROUTER_MODELS_URL).get("data", [])}
        except (OSError, ValueError, KeyError):
            pass  # expiry warning is best-effort
    notes = {}
    for t in reg.tiers.values():
        msgs = []
        if t.catalog:
            models = (cat.get(t.catalog) or {}).get("models") or {}
            m = models.get(t.model)
            if not models:
                msgs.append(f"no models.dev provider {t.catalog!r}")
            elif m is None:
                msgs.append(f"{t.model} not in models.dev {t.catalog}")
            elif m.get("structured_output") is False:
                msgs.append("no structured output (schema asks rely on repair)")
        if "openrouter.ai" in t.base_url and expiry:
            if t.model not in expiry:
                msgs.append("not listed by OpenRouter")
            elif expiry[t.model]:
                msgs.append(f"OpenRouter retires it {expiry[t.model]}")
        if msgs:
            notes[t.id] = "; ".join(msgs)
    return notes


def _openrouter_free_left(reg: Registry) -> int | None:
    """Requests left today on OpenRouter's shared free-model quota (all :free tiers share it), or None."""
    tier = next((t for t in reg.tiers.values() if "openrouter.ai" in t.base_url and t.model.endswith(":free")), None)
    if tier is None:
        return None
    try:
        req = urllib.request.Request(f"{tier.base_url.rstrip('/')}/key", headers={"Authorization": f"Bearer {_tier_key(tier)}"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return int(json.loads(r.read())["data"]["free_model_daily_requests"]["remaining"])
    except (OSError, ValueError, KeyError, TypeError, FleetError):
        return None


def doctor(reg: Registry, live: bool = False, catalog: bool = False) -> list[dict]:
    rows = []
    for w in reg.workers.values():
        ok, version = _probe(w.probe) if w.probe else (True, "-")
        note = ""
        if w.control == "acpx":
            try:
                acpx_entry()
            except FleetError as e:
                ok, note = False, str(e)
        until = health.cooldown_until(w.id)
        rows.append({"id": w.id, "kind": "agent", "cost": w.cost, "control": w.control, "enabled": w.enabled,
                     "opt_in": w.opt_in, "ok": ok and w.enabled, "version": version,
                     "cooldown_until": health.iso(until) if until else None, "note": note or w.notes[:60]})
    for t in reg.tiers.values():
        until = health.cooldown_until(t.id)
        row = {"id": t.id, "kind": "tier", "cost": t.cost, "control": t.cli or "http", "enabled": True, "opt_in": False,
               "model": t.model, "cooldown_until": health.iso(until) if until else None}
        if live:
            start = time.monotonic()
            try:
                # Reasoning tiers think before answering: 5 tokens left every tier empty (2026-11-06); 256 measured enough.
                # "id," = this tier alone: a fallback answering would hide a dead tier.
                r = ask(reg, t.id + ",", "Reply with OK", max_tokens=512, timeout_s=60)
                row.update(ok=bool(r.text), version=f"{r.model} {time.monotonic() - start:.1f}s", note="")
            except FleetError as e:
                row.update(ok=False, version="-", note=str(e)[:80])
        elif t.cli:
            found = bool(shutil.which(t.cli))
            row.update(ok=found, version=t.model, note=f"via {t.cli} (subscription)" if found else f"{t.cli} not installed")
        else:
            up = _tcp(t.base_url)
            row.update(ok=up, version=t.model, note="port open" if up else "port closed")
        if t.daily_cap:
            row["note"] = f"{calls_today(t.id)}/{t.daily_cap} calls today; {row.get('note', '')}".rstrip("; ")
        rows.append(row)
    if live and (left := _openrouter_free_left(reg)) is not None:
        for row in rows:
            if row["kind"] == "tier" and reg.tiers[row["id"]].model.endswith(":free"):
                row["note"] = f"{left} free OpenRouter requests left today; {row['note']}".rstrip("; ")
    if catalog:
        notes = catalog_notes(reg)
        for row in rows:
            if row["id"] in notes and row["kind"] == "tier":
                row["note"] = "; ".join(filter(None, [row["note"], "catalog: " + notes[row["id"]]]))
    return rows


def provider_rows(reg: Registry) -> list[dict]:
    """Which tiers can actually fire today: key present (names only, never values), adapter, cooldown."""
    from .llm import _secret_or_none

    rows = []
    for t in reg.tiers.values():
        rows.append({"id": t.id, "provider": t.catalog or t.adapter or t.base_url.split("/")[0],
                     "model": t.model, "cost": t.cost, "adapter": t.adapter or "-",
                     "key_env": t.key_env or "-", "key_present": (None if not t.key_env else bool(_secret_or_none(t.key_env))),
                     "cooldown_until": health.iso(u) if (u := health.cooldown_until(t.id)) else None})
    return rows
