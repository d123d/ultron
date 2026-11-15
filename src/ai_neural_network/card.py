import json
import os
import secrets
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from .registry import Worker  # noqa: F401  (render_prompt signature)


@dataclass
class TaskCard:
    goal: str
    id: str = ""
    title: str = ""
    cwd: str = ""
    paths: list[str] = field(default_factory=list)
    context_files: list[str] = field(default_factory=list)
    done_when: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    worker: str | None = None
    timeout_s: int | None = None
    capability: str = "code"
    worktree: bool = False

    def __post_init__(self) -> None:
        if not self.goal or not self.goal.strip():
            raise ValueError("Task card goal is required")
        if not self.id:
            self.id = f"t-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}"
        if not self.title:
            self.title = self.goal.strip()[:60]
        if not self.cwd:
            self.cwd = os.getcwd()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_CARD_KEYS = {f.name for f in TaskCard.__dataclass_fields__.values()}


def load_card(path_or_text: str | Path | TaskCard) -> TaskCard:
    if isinstance(path_or_text, TaskCard):
        return path_or_text
    candidate = Path(path_or_text)
    if isinstance(path_or_text, Path) or ("\n" not in str(path_or_text) and candidate.is_file()):
        text = candidate.read_text(encoding="utf-8")
        suffix = candidate.suffix.lower()
        if suffix == ".json":
            data = json.loads(text)
        elif suffix in {".yaml", ".yml"}:
            data = yaml.safe_load(text)
        else:
            data = {"goal": text}
    else:
        text = str(path_or_text)
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError:
            data = None
        if not isinstance(data, dict) or "goal" not in data:
            data = {"goal": text}
    unknown = set(data) - _CARD_KEYS
    if unknown:
        raise ValueError(f"Unknown task card key: {sorted(unknown)[0]}")
    return TaskCard(**data)


def render_prompt(card: TaskCard, worker: Worker) -> str:
    del worker
    scope = f" and touch only: {', '.join(card.paths)}" if card.paths else ""
    lines = [
        "You are a worker in the AI Fleet on Dustin's Windows workstation, operated headlessly by another agent.",
        f"Rules: nobody will answer questions, so decide and state assumptions. Work only in {card.cwd}{scope}.",
        "Never print secrets. Never kill processes you did not start. Never delete files permanently.",
        "Follow Ponytail ultra: the best code is never written. Question any part the need does not justify; smallest "
        "complete change, stdlib/existing patterns first, no unrequested abstractions, test for new logic. "
        "If checks are listed, run them before finishing. Never make a check pass by faking or shadowing its tool "
        "(no local pytest.py and the like): if a check's tool is missing, say so and finish with status partial.",
        'Help on tap: `C:\\AI\\bin\\ultron.cmd job vision "<question>" --image <file>` checks a render or screenshot '
        'you made (look before you call it done); `job quick|reason "<prompt>"` gives a second opinion.',
        "Write prose in caveman ultra: terse, no filler, technical substance exact. Code, commands, paths and the "
        "FLEET_RESULT line stay normal.",
        "Finish with one line exactly like:",
        'FLEET_RESULT: {"status": "done|partial|failed", "summary": "<one sentence>", "files": ["<changed paths>"], "notes": "<optional>"}',  # noqa: E501
        "",
        f"TASK ({card.id}): {card.goal}",
    ]
    if card.constraints:
        lines.extend(["CONSTRAINTS:", *[f"- {item}" for item in card.constraints]])
    if card.context_files:
        lines.extend(["CONTEXT FILES (read them first):", *[f"- {item}" for item in card.context_files]])
    if card.done_when:
        lines.extend(["DONE WHEN these commands succeed:", *[f"- {item}" for item in card.done_when]])
    return "\n".join(lines) + "\n"

