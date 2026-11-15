"""Task board backed by beads (bd): dependency-aware ready queue with atomic claims. Cards live in issue descriptions."""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from .card import TaskCard
from .procs import FleetError
from .registry import Registry

_CARD_RE = re.compile(r"```fleet-card\s*\n(.*?)\n```", re.S)


class Board:
    def __init__(self, reg: Registry, directory: str | Path | None = None, bd: str | None = None):
        self.dir = Path(directory or os.environ.get("FLEET_BOARD") or reg.board.get("dir") or r"C:\AI\state\fleet\board")
        self.bd = bd or os.environ.get("FLEET_BD") or reg.board.get("bd_path") or "bd"
        self.prefix = reg.board.get("prefix", "fl")

    def _run(self, *args: str, actor: str | None = None, check: bool = True) -> Any:
        argv = [self.bd, *args, "--json"]
        if actor:
            argv += ["--actor", actor]
        env = {**os.environ, "BD_NON_INTERACTIVE": "1"}
        try:
            p = subprocess.run(argv, cwd=str(self.dir), capture_output=True, text=True, encoding="utf-8",
                               errors="replace", env=env, timeout=120)
        except FileNotFoundError:
            raise FleetError(f"beads not found at {self.bd}") from None
        if check and p.returncode != 0:
            raise FleetError(f"bd {' '.join(args)} failed: {(p.stderr or p.stdout).strip()[:300]}")
        out = p.stdout.strip()
        try:
            return json.loads(out) if out else None
        except ValueError:
            return {"raw": out, "rc": p.returncode}

    def ensure(self) -> Path:
        self.dir.mkdir(parents=True, exist_ok=True)
        if not (self.dir / ".beads").exists():
            env = {**os.environ, "BD_NON_INTERACTIVE": "1"}
            p = subprocess.run([self.bd, "init", "--prefix", self.prefix], cwd=str(self.dir), capture_output=True,
                               text=True, env=env, timeout=120)
            if p.returncode != 0:
                raise FleetError(f"bd init failed: {(p.stderr or p.stdout).strip()[:300]}")
        return self.dir

    def add(self, card: TaskCard, deps: list[str] | tuple = (), priority: int = 2, hold: bool = False) -> str:
        self.ensure()
        desc = f"{card.goal}\n\n```fleet-card\n{json.dumps(card.to_dict(), indent=2)}\n```"
        args = ["create", card.title, "-t", "task", "-p", str(priority), "-d", desc]
        labels = [f"cap:{card.capability}"] + ([f"worker:{card.worker}"] if card.worker else [])
        if hold:  # work() skips this label: the card (and all that depend on it) waits for `board release`
            labels.append("fleet:needs-operator")
        args += ["-l", ",".join(labels)]
        if deps:
            args += ["--deps", ",".join(f"blocked-by:{d}" for d in deps)]
        data = self._run(*args)
        issue = data[0] if isinstance(data, list) else data
        return issue["id"]

    @staticmethod
    def _issues(data: Any) -> list[dict]:
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and isinstance(data.get("issues"), list):
            return data["issues"]
        return []

    def ready(self) -> list[dict]:
        self.ensure()
        return self._issues(self._run("ready"))

    def list(self, status: str | None = None) -> list[dict]:
        self.ensure()
        return self._issues(self._run("list", *(["--status", status] if status else [])))

    def get(self, issue_id: str) -> dict:
        data = self._run("show", issue_id)
        issues = self._issues(data)
        return issues[0] if issues else (data or {})

    def claim(self, issue_id: str, actor: str) -> bool:
        data = self._run("update", issue_id, "--claim", actor=actor, check=False)
        issues = self._issues(data)
        return bool(issues) and issues[0].get("assignee") == actor

    def release(self, issue_id: str) -> None:
        self._run("label", "remove", issue_id, "fleet:needs-operator")

    def close(self, issue_id: str, actor: str, reason: str) -> None:
        self._run("close", issue_id, "--reason", reason[:500], actor=actor)

    def reopen(self, issue_id: str, actor: str, note: str, label: str | None = None) -> None:
        args = ["update", issue_id, "--status", "open", "--assignee", "", "--notes", note[:1000], "--force"]
        if label:
            args += ["--add-label", label]
        self._run(*args, actor=actor)

    @staticmethod
    def card_of(issue: dict) -> TaskCard:
        m = _CARD_RE.search(issue.get("description") or "")
        if not m:
            return TaskCard(goal=issue.get("description") or issue.get("title") or "")
        data = json.loads(m.group(1))
        data["id"] = issue["id"]
        return TaskCard(**data)
