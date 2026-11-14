"""Fleet Leader election, wakeup, and multi-channel agent communication (GitHub, Telegram, Muse, OpenHuman)."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from . import health, registry
from .llm import _tier_key, calls_today
from .llm import ask as ask_llm
from .paths import state_dir
from .procs import FleetError
from .registry import Registry

LEADER_PRIORITY = [
    "claude-leader",
    "codex-leader",
    "gemini-argon",
    "github-leader",
    "agy-opus",
    "go-pro",
    "nim-ultra",
    "openrouter-ultra",
    "nim-mistral",
    "muse",
    "zen",
    "utility",
]

_COOLDOWN_ALIASES: dict[str, tuple[str, ...]] = {
    "claude-leader": ("claude-leader", "claude"),
    "codex-leader": ("codex-leader", "codex", "gpt"),
}


def _ai_root() -> Path:
    here = Path(__file__).resolve()
    if len(here.parents) > 4 and (here.parents[4] / "scripts").is_dir():
        return here.parents[4]
    return Path(r"C:\AI")


def _leader_state_path() -> Path:
    return state_dir() / "leader_state.json"


def _save_leader_state(data: dict[str, Any]) -> None:
    p = _leader_state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def elect_leader(reg: Registry | None = None) -> dict[str, Any]:
    """Choose the active Fleet Leader in priority order: Claude -> Codex -> Antigravity Argon -> GitHub -> Cloud."""
    reg = reg or registry.load()
    candidates: list[dict[str, Any]] = []
    chosen: dict[str, Any] | None = None
    for tid in LEADER_PRIORITY:
        tier = reg.tiers.get(tid)
        if tier is None:
            candidates.append({"tier": tid, "eligible": False, "reason": "not in registry"})
            continue
        reason = ""
        aliases = _COOLDOWN_ALIASES.get(tid, (tid,))
        until = max((u for a in aliases if (u := health.cooldown_until(a)) is not None), default=None)
        if until is not None:
            reason = f"cooldown until {health.iso(until)}"
        elif tier.daily_cap and calls_today(tid) >= tier.daily_cap:
            reason = f"daily cap reached ({tier.daily_cap})"
        elif tier.cli and not shutil.which(tier.cli):
            reason = f"{tier.cli} not on PATH"
        elif tier.key_env:
            try:
                _tier_key(tier)
            except FleetError as e:
                reason = str(e)
        row = {
            "tier": tid,
            "model": tier.model,
            "effort": tier.effort or "",
            "cli": tier.cli or "http",
            "eligible": not reason,
            "reason": reason or "ready",
        }
        candidates.append(row)
        if not reason and chosen is None:
            chosen = row
    return {
        "elected": chosen["tier"] if chosen else None,
        "model": chosen["model"] if chosen else None,
        "effort": chosen["effort"] if chosen else None,
        "candidates": candidates,
    }


def _dispatch_channel(channel: str, message: str, *, repo: str = "d123d/agent-comms",
                      issue: int | None = None, title: str | None = None) -> list[dict[str, Any]]:
    sent: list[dict[str, Any]] = []
    ch = (channel or "none").lower()
    if ch in ("", "none"):
        return sent
    if ch in ("telegram", "both") or "telegram" in ch:
        sent.append(comm_message("telegram", message))
    if ch in ("github", "both") or "github" in ch:
        sent.append(comm_message("github", message, repo=repo, issue=issue,
                                 title=title or "Fleet Leader Update", labels=["fleet-leader"]))
    return sent


def wake_leader(report: str, *, system: str | None = None, channel: str = "none",
                repo: str = "d123d/agent-comms", issue: int | None = None,
                reg: Registry | None = None) -> dict[str, Any]:
    """Wake the highest-priority available Fleet Leader with a status report."""
    reg = reg or registry.load()
    election = elect_leader(reg)
    eligible = [c["tier"] for c in election["candidates"] if c["eligible"]]
    if not eligible:
        raise FleetError("no eligible Fleet Leader candidate available (all in cooldown)")
    sys_prompt = system or (
        "You are the elected AI Fleet Leader waking on a report from OpenHuman (Second in Command). "
        "Review the status, give crisp next directives, and state whether sleep can resume."
    )
    last_err: Exception | None = None
    result = None
    for tid in eligible:
        try:
            result = ask_llm(reg, tid + ",", report, system=sys_prompt, timeout_s=180)
            break
        except FleetError as e:
            last_err = e
            continue
    if result is None:
        raise FleetError(f"all Fleet Leader candidates failed: {last_err}")
    payload: dict[str, Any] = {
        "elected": election["elected"],
        "used_tier": result.tier,
        "model": result.model,
        "ts": health.iso(health.now()),
        "reply": result.text,
        "duration_s": result.duration_s,
    }
    _save_leader_state({**payload, "reply_preview": result.text[:500]})
    if channel and channel.lower() != "none":
        payload["channels"] = _dispatch_channel(
            channel, f"[Fleet Leader {result.tier} / {result.model}]\n{result.text[:1500]}",
            repo=repo, issue=issue, title=f"Fleet Leader Report ({result.tier})",
        )
    return payload


def _run_cmd(argv: list[str], *, timeout_s: int = 45) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout_s)


def comm_message(to: str, message: str = "", *, repo: str = "d123d/agent-comms", issue: int | None = None,
                 title: str | None = None, labels: list[str] | None = None,
                 read: bool = False, channel: str = "none", reg: Registry | None = None) -> dict[str, Any]:
    """Send or read agent/leader messages across Muse, GitHub, Telegram, OpenHuman, or the elected Leader."""
    target = to.lower().strip()
    root = _ai_root()

    if target == "leader":
        return wake_leader(message, channel=channel, repo=repo, issue=issue, reg=reg)

    if target == "muse":
        if not message:
            raise FleetError("--message is required when talking to muse")
        reg = reg or registry.load()
        res = ask_llm(reg, "muse", message,
                      system="You are Muse, creative & strategic AI collaborator in AI Fleet.", timeout_s=180)
        out: dict[str, Any] = {"to": "muse", "tier": res.tier, "model": res.model,
                               "reply": res.text, "duration_s": res.duration_s}
        if channel and channel.lower() != "none":
            out["channels"] = _dispatch_channel(
                channel, f"[Muse / {res.model}]\n{res.text[:1500]}",
                repo=repo, issue=issue, title=title or "Muse Fleet Note",
            )
        return out

    if target == "github":
        gh = shutil.which("gh")
        if not gh:
            raise FleetError("gh CLI not found on PATH")
        if read:
            p = _run_cmd([gh, "issue", "list", "--repo", repo, "--state", "open",
                          "--json", "number,title,labels,body,comments,updatedAt", "--limit", "20"])
            if p.returncode != 0:
                raise FleetError(f"gh issue list failed: {(p.stderr or p.stdout).strip()[:300]}")
            return {"to": "github", "repo": repo, "action": "list", "issues": json.loads(p.stdout or "[]")}
        if not message:
            raise FleetError("--message is required when posting to github")
        if issue is not None:
            p = _run_cmd([gh, "issue", "comment", str(issue), "--repo", repo, "--body", message])
            if p.returncode != 0:
                raise FleetError(f"gh issue comment failed: {(p.stderr or p.stdout).strip()[:300]}")
            return {"to": "github", "repo": repo, "action": "comment", "issue": issue, "url": p.stdout.strip()}
        argv = [gh, "issue", "create", "--repo", repo, "--title", title or "AI Fleet Note", "--body", message]
        for lbl in (labels or []):
            argv += ["--label", lbl]
        p = _run_cmd(argv)
        if p.returncode != 0:
            raise FleetError(f"gh issue create failed: {(p.stderr or p.stdout).strip()[:300]}")
        return {"to": "github", "repo": repo, "action": "create", "url": p.stdout.strip()}

    if target == "telegram":
        if read:
            hist = root / "scripts" / "telegram_history.py"
            dm = root / "projects" / "openhuman-ops" / "scripts" / "dm_days.py"
            if hist.is_file():
                uv = shutil.which("uv")
                runners = ([[uv, "run"]] if uv else []) + [[sys.executable]]
                for runner in runners:
                    p = _run_cmd([*runner, str(hist), "read", "--limit", "10"])
                    if p.returncode == 0:
                        return {"to": "telegram", "action": "read", "output": p.stdout.strip()}
            if dm.is_file():
                p = _run_cmd([sys.executable, str(dm), "2", "--limit", "10"])
                if p.returncode == 0:
                    return {"to": "telegram", "action": "read", "output": p.stdout.strip()}
            raise FleetError("telegram read failed (neither telegram_history.py nor dm_days.py succeeded)")
        if not message:
            raise FleetError("--message is required when sending to telegram")
        sender = root / "scripts" / "telegram_send.py"
        if not sender.is_file():
            raise FleetError(f"telegram sender missing: {sender}")
        p = _run_cmd([sys.executable, str(sender), "--text", message])
        if p.returncode != 0:
            raise FleetError(f"telegram send failed: {(p.stderr or p.stdout).strip()[:300]}")
        return {"to": "telegram", "action": "send", "output": p.stdout.strip()}

    if target == "openhuman":
        if not message:
            raise FleetError("--message is required when messaging openhuman")
        oh_msg = root / "projects" / "openhuman-ops" / "scripts" / "oh_msg.py"
        if not oh_msg.is_file():
            raise FleetError(f"oh_msg helper missing: {oh_msg}")
        p = _run_cmd([sys.executable, str(oh_msg), "send", message], timeout_s=180)
        if p.returncode != 0:
            raise FleetError(f"openhuman send failed: {(p.stderr or p.stdout).strip()[:300]}")
        return {"to": "openhuman", "action": "send", "output": p.stdout.strip()}

    raise FleetError(f"unknown comm target {to!r}; choose muse, github, telegram, openhuman, or leader")
