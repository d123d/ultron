"""Run a task card's done_when checks. The fleet trusts these, not the worker's claim."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .procs import redact


def run_checks(commands: list[str], cwd: str | Path, timeout_s: int = 600) -> dict[str, Any]:
    if not commands:
        return {"ran": False, "passed": None, "results": []}
    results = []
    for cmd in commands:
        if not shutil.which("python"):
            # Checks are authored with `python` (Windows fleet PCs have it); many Linux
            # boxes only ship python3. Run them under this interpreter instead of failing.
            cmd = re.sub(r"(^|[;&|]\s*)python(?=\s|$)", lambda m: f"{m.group(1)}{sys.executable}", cmd)
        try:
            p = subprocess.run(cmd, shell=True, cwd=str(cwd), capture_output=True, timeout=timeout_s)
            out = (p.stdout + p.stderr).decode("utf-8", errors="replace")
            rc = p.returncode
        except subprocess.TimeoutExpired:
            out, rc = f"check timed out after {timeout_s}s", -1
        results.append({"cmd": cmd, "rc": rc, "tail": redact(out[-2000:])})
    return {"ran": True, "passed": all(r["rc"] == 0 for r in results), "results": results}


REFUTED = "done_when refuted"
_TESTISH = re.compile(r"(^|/)(tests?|__tests__|spec)/|(^|/)test_[^/]*$|_test\.[^/]+$|\.(test|spec)\.[^/]+$")


def edited_checks(changed: list[str], commands: list[str], scope: list[str], existed: Callable[[str], bool]) -> list[str]:
    """Pre-existing files the checks depend on that the worker changed outside its card scope.

    A worker that edits the test its done_when runs can pass by weakening it (idea: Enderfga/claw-orchestrator).
    New test files are fine; files listed in card.paths are fine.
    shortcut: guards test-named files and files named in done_when, not conftest/pytest config; widen if gamed.
    """
    norm = [p.replace("\\", "/").strip("/") for p in scope]
    text = " ".join(commands).replace("\\", "/")

    def in_scope(f: str) -> bool:
        return any(f == p or f.startswith(p + "/") or f.endswith("/" + p) for p in norm if p)

    hits = []
    for f in changed:
        f = f.replace("\\", "/")
        named = f in text or f.rsplit("/", 1)[-1] in text.split() or ("/" + f.rsplit("/", 1)[-1]) in text
        if (named or _TESTISH.search(f)) and not in_scope(f) and existed(f):
            hits.append(f)
    return hits
