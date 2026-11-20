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
        "You are a worker for Ultron on this Windows workstation, operated headlessly by another agent.",
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
    if skills := skills_for(card.goal):
        lines.extend(
            [
                "SKILLS (how this machine does this kind of work; read the ones that fit before you start):",
                *[f"- {p}" for p in skills],
            ]
        )
    return "\n".join(lines) + "\n"


def skills_for(text: str, limit: int = 10) -> list[str]:
    """Skill files (and the machine tool registry) whose topic keywords appear in the task text.

    config/skills.yaml maps topics to skills; the skills themselves live in the machine's skill library
    (ULTRON_SKILLS_DIR or `library:`), not in this repo. Missing library or skill: silently skipped.
    """
    from .paths import config_dir

    try:
        cfg = yaml.safe_load((config_dir() / "skills.yaml").read_text(encoding="utf-8")) or {}
    except OSError:
        return []
    library = Path(os.environ.get("ULTRON_SKILLS_DIR") or cfg.get("library") or "")
    low = text.lower()
    names: list[str] = []
    for topic in (cfg.get("topics") or {}).values():
        if any(k in low for k in topic.get("match", [])):
            names += [n for n in topic.get("skills", []) if n not in names]
    paths = [str(p) for n in names if (p := library / n / "SKILL.md").is_file()][:limit]
    always = [a for a in cfg.get("always", []) if Path(a).is_file()] if paths else []
    return always + paths
