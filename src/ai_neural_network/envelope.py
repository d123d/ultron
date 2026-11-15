"""Result envelope returned for every fleet run, plus the worker FLEET_RESULT line parser."""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any

_RESULT_RE = re.compile(r"^[\s`>*_-]*FLEET_RESULT:\s*(.*)$")


@dataclass
class Envelope:
    run_id: str
    card_id: str
    worker: str
    control: str
    status: str  # ok | failed | timeout | skipped | error
    reason: str = ""
    summary: str = ""
    worker_status: str | None = None
    files_changed: list[str] = field(default_factory=list)
    diffstat: str = ""
    verify: dict[str, Any] = field(default_factory=lambda: {"ran": False, "passed": None, "results": []})
    duration_s: float = 0.0
    usage: dict[str, Any] = field(default_factory=dict)
    session_id: str | None = None
    log_dir: str = ""
    started_at: str = ""
    attempts: list[dict[str, Any]] = field(default_factory=list)
    worktree: dict[str, Any] | None = None

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=False, indent=2)


def parse_fleet_result(text: str) -> dict[str, Any] | None:
    """Return the JSON of the LAST `FLEET_RESULT:` line in text, or None. Never raises."""
    for line in reversed((text or "").splitlines()):
        m = _RESULT_RE.match(line)
        if not m:
            continue
        raw = m.group(1).strip().strip("`").strip()
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            start, end = raw.find("{"), raw.rfind("}")
            if start < 0 or end <= start:
                return None
            try:
                data = json.loads(raw[start : end + 1])
            except ValueError:
                return None
        return data if isinstance(data, dict) else None
    return None


HANDOFF_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["status", "summary"],
    "properties": {
        # done|partial|failed are what the worker prompt (card.render_prompt) asks for; the rest are envelope states
        "status": {"type": "string", "enum": ["done", "partial", "failed", "ok", "blocked", "timeout", "error"]},
        "summary": {"type": "string", "minLength": 1},
        "files_changed": {"type": "array", "items": {"type": "string"}},
    },
    "additionalProperties": True,
}


def validate_handoff(payload: dict[str, Any]) -> tuple[bool, list[str]]:
    if not isinstance(payload, dict):
        return False, ["payload must be a dict"]
    import jsonschema

    errors = sorted(jsonschema.Draft7Validator(HANDOFF_SCHEMA).iter_errors(payload), key=lambda e: list(e.path))
    return not errors, [e.message for e in errors]


def parse_valid_fleet_result(text: str) -> tuple[dict[str, Any] | None, list[str]]:
    payload = parse_fleet_result(text)
    if payload is None:
        return None, ["no valid FLEET_RESULT found"]
    ok, errors = validate_handoff(payload)
    if not ok:
        return None, errors
    return payload, []
