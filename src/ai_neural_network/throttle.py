"""Cross-process per-provider rate limiting: spreads LLM requests so no single provider is overheated.

State (throttle.json) maps provider keys to start-time lists; a lock file (throttle.lock) serialises
updates across processes. Guarantees per provider (config in workers.yaml `throttle:` section):
  - min_interval_s: minimum spacing between request starts (from any fleet process)
  - max_concurrent:  maximum requests in flight (in-flight = starts newer than slot_ttl_s, so crashes self-heal)
  - max_queue_s:     how long a caller waits for a free slot before failing
"""
from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

from .paths import throttle_path
from .procs import FleetError

DEFAULTS: dict[str, float] = {"min_interval_s": 1.0, "max_concurrent": 2, "max_queue_s": 120.0, "slot_ttl_s": 900.0}
_LOCK_WAIT_S = 10.0


class Busy(FleetError):
    """No free slot on the provider within max_queue_s: callers may try another provider."""
_CFG_KEYS = {"min_interval_s", "max_concurrent", "max_queue_s", "slot_ttl_s"}


def provider_key(base_url: str) -> str:
    """Group tiers by upstream provider: host:port/first-path-segment (gate providers live in the first segment)."""
    u = urlsplit(base_url)
    key = f"{u.hostname}:{u.port or (443 if u.scheme == 'https' else 80)}"
    seg = (u.path or "").strip("/").split("/")[0]
    return f"{key}/{seg}" if seg else key


def config_for(reg, base_url: str) -> dict[str, float]:
    """Throttle config for a provider: exact key match, else the registry default, else library defaults."""
    cfg = dict(DEFAULTS)
    throttle = getattr(reg, "throttle", None) or {}
    if isinstance(throttle.get("default"), dict):
        cfg.update(throttle["default"])
    key = provider_key(base_url)
    if key in throttle:
        if unknown := set(throttle[key]) - _CFG_KEYS:
            raise FleetError(f"unknown throttle key for {key}: {sorted(unknown)[0]}")
        cfg.update(throttle[key])
    return cfg


def _lock_file() -> Path:
    return throttle_path().with_suffix(".lock")


@contextmanager
def _locked():
    import sys

    path = _lock_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+b") as f:
        f.seek(0)
        deadline = time.monotonic() + _LOCK_WAIT_S
        while True:
            try:
                if sys.platform == "win32":
                    import msvcrt

                    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise FleetError(f"throttle lock stuck for {_LOCK_WAIT_S}s ({path})") from None
                time.sleep(0.02)
        try:
            yield
        finally:
            try:
                f.seek(0)
                if sys.platform == "win32":
                    import msvcrt

                    msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass


def _load() -> dict:
    try:
        return json.loads(throttle_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(data: dict) -> None:
    path = throttle_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.stem}_{os.getpid()}_{time.monotonic_ns()}.tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    for _ in range(5):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.02)
    os.replace(tmp, path)


def _prune(entry: dict, now: float, ttl: float) -> int:
    entry["starts"] = [s for s in entry.get("starts", []) if now - s < ttl]
    return len(entry["starts"])


def _try_acquire(key: str, cfg: dict[str, float]) -> float | None:
    """Under lock: claim a slot if spacing and concurrency allow. Returns the start timestamp, else None."""
    with _locked():
        data = _load()
        now = time.monotonic()
        entry = data.setdefault(key, {"starts": [], "last_start": 0.0})
        if entry.get("last_start", 0.0) > now:
            # monotonic reset (reboot) left a future timestamp; without this
            # every acquire fails until the clock catches up (2026-10-16: stuck 32 h)
            entry["last_start"] = 0.0
        in_flight = _prune(entry, now, cfg["slot_ttl_s"])
        if in_flight < cfg["max_concurrent"] and now - entry["last_start"] >= cfg["min_interval_s"]:
            entry["starts"].append(now)
            entry["last_start"] = now
            _save(data)
            return now
        return None


def _release(key: str, start: float) -> None:
    with _locked():
        data = _load()
        entry = data.get(key)
        if not entry:
            return
        try:
            entry["starts"].remove(start)
        except ValueError:
            pass  # already pruned as stale
        _save(data)


@contextmanager
def slot(reg, base_url: str):
    """Claim one request slot on the provider serving base_url; waits (bounded) if it is too hot."""
    key = provider_key(base_url)
    cfg = config_for(reg, base_url)
    deadline = time.monotonic() + cfg["max_queue_s"]
    start = None
    while True:
        start = _try_acquire(key, cfg)
        if start is not None:
            break
        if time.monotonic() > deadline:
            raise Busy(f"provider {key} still busy after {cfg['max_queue_s']:.0f}s "
                             f"(min_interval {cfg['min_interval_s']}s, max_concurrent {cfg['max_concurrent']})")
        time.sleep(min(0.25, max(0.02, cfg["min_interval_s"] / 4)))
    try:
        yield key
    finally:
        _release(key, start)


def peek(reg, base_urls: list[str]) -> dict[str, dict]:
    """Advisory snapshot (no lock held): provider key -> in-flight count, seconds since last start."""
    out: dict[str, dict] = {}
    if not base_urls:
        return out
    with _locked():
        data = _load()
        now = time.monotonic()
        for url in base_urls:
            key = provider_key(url)
            entry = data.get(key) or {}
            snap = dict(entry)
            in_flight = _prune(snap, now, config_for(reg, url)["slot_ttl_s"])
            out[key] = {"in_flight": in_flight, "since_last_start": now - entry.get("last_start", 0.0),
                        "max_concurrent": config_for(reg, url)["max_concurrent"]}
    return out


def spread_order(reg, tier_ids: list[str]) -> list[str]:
    """Order candidate tiers so parallel asks fan out: ready providers first, least-recently-used first."""
    if not tier_ids:
        return []
    by_id = {t.id: t for t in reg.tiers.values()}
    snap = peek(reg, [by_id[t].base_url for t in tier_ids if t in by_id])
    cost_rank = {c: i for i, c in enumerate(reg.cost_order)}

    def score(tid: str) -> tuple:
        t = by_id.get(tid)
        info = snap.get(provider_key(t.base_url)) if t else {}
        ready = info.get("in_flight", 0) < info.get("max_concurrent", DEFAULTS["max_concurrent"])
        return (0 if ready else 1, cost_rank.get(t.cost, 99), -(info.get("since_last_start") or 0), tid)

    return sorted(tier_ids, key=score)
