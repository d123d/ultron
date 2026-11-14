"""Subprocess execution with timeouts that only ever kill the process tree we started, plus secret redaction."""
from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

_SECRET_NAME = re.compile(r"KEY|TOKEN|SECRET|PASSWORD", re.IGNORECASE)


class FleetError(RuntimeError):
    """User-facing fleet failure."""


@dataclass
class ProcResult:
    rc: int | None
    stdout: str
    stderr: str
    timed_out: bool
    duration_s: float


def _secret_values() -> list[str]:
    vals = {v for k, v in os.environ.items() if _SECRET_NAME.search(k) and v and len(v) >= 8}
    return sorted(vals, key=len, reverse=True)


def redact(text: str) -> str:
    for value in _secret_values():
        if value in text:
            text = text.replace(value, "[REDACTED]")
    return text


def ensure_full_path() -> list[str]:
    """Merge machine+user PATH from the Windows registry into this process PATH (additive, deduped).

    Operator shells launched with a stripped environment (e.g. machine PATH only) otherwise hide every
    agent CLI (npm, acpx, opencode, ...) that lives in a user-PATH directory, and the fleet reports
    'not installed' everywhere. Returns the entries added. No-op outside Windows."""
    if sys.platform != "win32":
        return []
    import winreg

    raw: list[str] = []
    keys = [(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
            (winreg.HKEY_CURRENT_USER, r"Environment")]
    for hive, subkey in keys:
        try:
            with winreg.OpenKey(hive, subkey) as k:
                value, _ = winreg.QueryValueEx(k, "Path")
            raw.extend(str(value).split(";"))
        except OSError:
            continue
    current = {os.path.expandvars(p).rstrip("\\").lower() for p in os.environ.get("PATH", "").split(";") if p}
    added: list[str] = []
    for entry in raw:
        entry = entry.strip()
        if not entry:
            continue
        norm = os.path.expandvars(entry).rstrip("\\").lower()
        if norm not in current:
            current.add(norm)
            added.append(entry)
    if added:
        os.environ["PATH"] = os.environ.get("PATH", "").rstrip(";") + ";" + ";".join(added)
    return added


def kill_tree(pid: int) -> None:
    """Kill pid and its children. Only call with a pid this process started."""
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True, check=False)
    else:
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


def run_process(
    argv: list[str],
    cwd: str | Path,
    log_dir: str | Path,
    *,
    env: dict[str, str] | None = None,
    timeout_s: float = 1800,
    stdin_text: str | None = None,
) -> ProcResult:
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    out_path, err_path = log_dir / "stdout.txt", log_dir / "stderr.txt"
    full_env = {**os.environ, **(env or {})}
    kwargs: dict = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    start = time.monotonic()
    timed_out = False
    with open(out_path, "wb") as out, open(err_path, "wb") as err:
        try:
            proc = subprocess.Popen(
                argv,
                cwd=str(cwd),
                env=full_env,
                stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
                stdout=out,
                stderr=err,
                **kwargs,
            )
        except FileNotFoundError as e:
            raise FleetError(f"executable not found: {argv[0]} ({e})") from None
        try:
            if stdin_text is not None:
                assert proc.stdin is not None
                proc.stdin.write(stdin_text.encode("utf-8"))
                proc.stdin.close()
            proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            timed_out = True
            kill_tree(proc.pid)
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                pass
    duration = time.monotonic() - start
    stdout = redact(out_path.read_bytes().decode("utf-8", errors="replace"))
    stderr = redact(err_path.read_bytes().decode("utf-8", errors="replace"))
    out_path.write_text(stdout, encoding="utf-8")
    err_path.write_text(stderr, encoding="utf-8")
    return ProcResult(None if timed_out else proc.returncode, stdout, stderr, timed_out, round(duration, 2))
