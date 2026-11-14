"""Per-worker health and quota cooldowns persisted in health.json."""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from .paths import health_path

_LOCK = threading.RLock()


def now() -> datetime:
    return datetime.now(UTC)


def iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def load() -> dict[str, dict[str, Any]]:
    path = health_path()
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(data: dict[str, Any]) -> None:
    path = health_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".health-", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    for attempt in range(20):  # Windows refuses a replace while another thread reads or replaces the file
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 19:
                os.unlink(tmp)
                raise
            time.sleep(0.05)


def _entry(data: dict, worker: str) -> dict[str, Any]:
    return data.setdefault(worker, {"cooldown_until": None, "reason": None, "last_ok": None, "last_error": None})


def cooldown_until(worker: str) -> datetime | None:
    until = load().get(worker, {}).get("cooldown_until")
    if not until:
        return None
    dt = datetime.fromisoformat(until)
    return dt if dt > now() else None


def in_cooldown(worker: str) -> bool:
    return cooldown_until(worker) is not None


def set_cooldown(worker: str, seconds: int, reason: str) -> None:
    with _LOCK:
        data = load()
        e = _entry(data, worker)
        e["cooldown_until"] = iso(now() + timedelta(seconds=seconds))
        e["reason"] = reason
        e["last_error"] = reason
        _save(data)


def clear_cooldown(worker: str) -> None:
    with _LOCK:
        data = load()
        e = _entry(data, worker)
        e["cooldown_until"], e["reason"] = None, None
        _save(data)


def record_ok(worker: str) -> None:
    with _LOCK:
        data = load()
        _entry(data, worker)["last_ok"] = iso(now())
        _save(data)


def record_error(worker: str, msg: str) -> None:
    with _LOCK:
        data = load()
        _entry(data, worker)["last_error"] = msg[:500]
        _save(data)


def detect_quota(text: str, patterns: list[str]) -> str | None:
    """Return the first quota pattern found (case-insensitive) in the last 20k chars of text, else None."""
    hay = (text or "")[-20000:].lower()
    for p in patterns:
        if p.lower() in hay:
            return p
    return None
