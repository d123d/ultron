"""Git snapshots so the fleet can report exactly which files a worker changed."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path


def _git(cwd: str | Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, check=False)


def repo_root(cwd: str | Path) -> Path | None:
    p = _git(cwd, "rev-parse", "--show-toplevel")
    return Path(p.stdout.decode().strip()) if p.returncode == 0 else None


@dataclass
class Snapshot:
    root: Path | None
    head: str | None = None
    status: dict[str, str] = field(default_factory=dict)


def _status(cwd: str | Path) -> dict[str, str]:
    p = _git(cwd, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    entries: dict[str, str] = {}
    parts = p.stdout.decode("utf-8", errors="replace").split("\0")
    i = 0
    while i < len(parts):
        item = parts[i]
        if len(item) > 3:
            code, path = item[:2], item[3:]
            entries[path] = code
            if code[0] in "RC":  # rename/copy: next field is the source path
                i += 1
        i += 1
    return entries


def snapshot(cwd: str | Path) -> Snapshot:
    root = repo_root(cwd)
    if root is None:
        return Snapshot(None)
    head = _git(root, "rev-parse", "HEAD")
    sha = head.stdout.decode().strip() if head.returncode == 0 else None
    return Snapshot(root, sha, _status(root))


def diff_since(snap: Snapshot) -> tuple[list[str], str]:
    """Files changed since the snapshot (working tree + new commits) and a shortstat line."""
    if snap.root is None:
        return [], ""
    after = _status(snap.root)
    changed = {p for p, code in after.items() if snap.status.get(p) != code}
    changed |= {p for p in snap.status if p not in after}  # was dirty, now committed or reverted
    stat = ""
    if snap.head:
        names = _git(snap.root, "diff", "--name-only", f"{snap.head}..HEAD")
        changed |= {n for n in names.stdout.decode().splitlines() if n}
        shortstat = _git(snap.root, "diff", "--shortstat", snap.head)
        stat = shortstat.stdout.decode().strip()
    untracked_new = [p for p in changed if after.get(p) == "??"]
    if untracked_new:
        stat = (stat + f"; {len(untracked_new)} new untracked file(s)").strip("; ")
    return sorted(changed), stat


def existed_at(snap: Snapshot, path: str) -> bool:
    """True if path was tracked at the snapshot's HEAD."""
    return bool(snap.root and snap.head) and _git(snap.root, "cat-file", "-e", f"{snap.head}:{path}").returncode == 0
