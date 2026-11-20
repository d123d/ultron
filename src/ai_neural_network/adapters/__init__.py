"""Worker control adapters: build the command for a worker and parse its output."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from ..card import TaskCard
from ..procs import FleetError, ProcResult
from ..registry import Worker

ARG_PROMPT_LIMIT = 24000


@dataclass
class AdapterResult:
    reply: str = ""
    usage: dict[str, Any] = field(default_factory=dict)
    session_id: str | None = None
    error: str | None = None


@dataclass
class Invocation:
    argv: list[str]
    env: dict[str, str] = field(default_factory=dict)
    stdin_text: str | None = None


def _exe(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise FleetError(f"{name} not found on PATH")
    return path


def _agy_prompt(prompt: str) -> tuple[list[str], str | None]:
    """agy prompt args and stdin. Long prompts go on stdin as one stream-json turn: a file path fails when tools are off."""
    if len(prompt) <= ARG_PROMPT_LIMIT:
        return ["-p", prompt, "--output-format", "json"], None
    turn = json.dumps({"event": "user", "message": {"content": prompt}}) + "\n"
    return ["--print=", "--input-format", "stream-json", "--output-format", "stream-json"], turn


@lru_cache(maxsize=1)
def acpx_entry() -> str:
    """Path to acpx's JS entry (avoids running the npm .cmd shim)."""
    roots: list[Path] = []
    npm = shutil.which("npm")
    if npm:
        try:
            out = subprocess.run([npm, "root", "-g"], capture_output=True, text=True, timeout=30, shell=npm.endswith(".cmd"))
            if out.stdout.strip():
                roots.append(Path(out.stdout.strip()))
        except (OSError, subprocess.SubprocessError):
            pass
    node = shutil.which("node")
    if node:  # npm prefix can be the install dir itself (node-vX/node_modules), not %APPDATA%\npm
        roots.append(Path(node).resolve().parent / "node_modules")
    roots.append(Path.home() / "AppData" / "Roaming" / "npm" / "node_modules")
    for root in roots:
        entry = root / "acpx" / "dist" / "cli.js"
        if entry.is_file():
            return str(entry)
    raise FleetError("acpx not installed (npm install -g acpx)")


class Adapter:
    def build(
        self, card: TaskCard, worker: Worker, prompt: str, prompt_file: Path, cwd: str, timeout_s: int, **opts: Any
    ) -> Invocation:
        raise NotImplementedError

    def parse(self, proc: ProcResult) -> AdapterResult:
        return AdapterResult(reply=proc.stdout.strip(), error=None if proc.rc == 0 else (proc.stderr.strip()[-500:] or None))


class AcpxAdapter(Adapter):
    def build(self, card, worker, prompt, prompt_file, cwd, timeout_s, **opts):
        if not worker.acpx_agent:
            raise FleetError(f"worker {worker.id} has control acpx but no acpx_agent")
        argv = [
            _exe("node"),
            acpx_entry(),
            "--format",
            "json",
            "--approve-all",
            "--cwd",
            cwd,
            "--timeout",
            str(timeout_s),
            worker.acpx_agent,
            "exec",
            "-f",
            str(prompt_file),
        ]
        return Invocation(argv)

    def parse(self, proc):
        reply: list[str] = []
        res = AdapterResult()
        for line in proc.stdout.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            params = msg.get("params") or {}
            if res.session_id is None and isinstance(params, dict) and params.get("sessionId"):
                res.session_id = params["sessionId"]
            if msg.get("method") == "session/update":
                upd = params.get("update") or {}
                kind = upd.get("sessionUpdate")
                if kind == "agent_message_chunk":
                    reply.append((upd.get("content") or {}).get("text", ""))
                elif kind == "usage_update" and isinstance(upd.get("cost"), dict):
                    res.usage["cost_usd"] = upd["cost"].get("amount")
            if isinstance(msg.get("result"), dict) and isinstance(msg["result"].get("usage"), dict):
                u = msg["result"]["usage"]
                res.usage["input_tokens"] = u.get("inputTokens")
                res.usage["output_tokens"] = u.get("outputTokens")
            if isinstance(msg.get("error"), dict):
                res.error = msg["error"].get("message") or json.dumps(msg["error"])[:500]
        res.reply = "".join(reply).strip()
        if res.error is None and proc.rc not in (0, None) and not res.reply:
            res.error = proc.stderr.strip()[-500:] or f"exit {proc.rc}"
        return res


class AgyAdapter(Adapter):
    def build(self, card, worker, prompt, prompt_file, cwd, timeout_s, **opts):
        args, stdin_text = _agy_prompt(prompt)
        return Invocation([_exe("agy"), *args, "--dangerously-skip-permissions"], stdin_text=stdin_text)

    def parse(self, proc):
        text = proc.stdout.strip()
        for line in reversed(text.splitlines()):  # stream-json: the last "result" event holds the json-mode object
            if line.startswith('{"event":"result"'):
                text = json.dumps(json.loads(line)["result"])
                break
        start, end = text.find("{"), text.rfind("}")
        if start < 0:
            return AdapterResult(error=(proc.stderr.strip()[-500:] or "agy produced no JSON"))
        try:
            data = json.loads(text[start : end + 1])
        except ValueError:
            return AdapterResult(reply=text, error="agy output was not valid JSON")
        usage = data.get("usage") or {}
        err = data.get("error") or (None if data.get("status") == "SUCCESS" else data.get("status"))
        return AdapterResult(
            reply=(data.get("response") or "").strip(),
            session_id=data.get("conversation_id"),
            usage={"input_tokens": usage.get("input_tokens"), "output_tokens": usage.get("output_tokens")},
            error=err or None,
        )


class ClaudeAdapter(Adapter):
    def build(self, card, worker, prompt, prompt_file, cwd, timeout_s, **opts):
        if not opts.get("allow_claude"):
            raise FleetError("claude is operator-only; pass --allow-claude to use it as a worker")
        budget = float(opts.get("budget_usd") or 1.0)
        argv = [
            _exe("claude"),
            "-p",
            "--output-format",
            "json",
            "--permission-mode",
            "acceptEdits",
            "--max-turns",
            "40",
            "--max-budget-usd",
            f"{budget:g}",
        ]
        return Invocation(argv, stdin_text=prompt)

    def parse(self, proc):
        text = proc.stdout.strip()
        try:
            data = json.loads(text[text.find("{") : text.rfind("}") + 1])
        except ValueError:
            return AdapterResult(reply=text, error=proc.stderr.strip()[-500:] or "claude output was not JSON")
        return AdapterResult(
            reply=str(data.get("result") or "").strip(),
            session_id=data.get("session_id"),
            usage={"cost_usd": data.get("total_cost_usd")},
            error=str(data.get("result"))[:500] if data.get("is_error") else None,
        )


class CommandAdapter(Adapter):
    def build(self, card, worker, prompt, prompt_file, cwd, timeout_s, **opts):
        if not worker.argv:
            raise FleetError(f"worker {worker.id} has control command but no argv")
        argv = [a.replace("{prompt_file}", str(prompt_file)).replace("{cwd}", cwd) for a in worker.argv]
        return Invocation(argv)


_ADAPTERS: dict[str, Adapter] = {
    "acpx": AcpxAdapter(),
    "agy": AgyAdapter(),
    "claude": ClaudeAdapter(),
    "command": CommandAdapter(),
}


def get_adapter(control: str) -> Adapter:
    try:
        return _ADAPTERS[control]
    except KeyError:
        raise FleetError(f"unknown control {control!r}") from None
