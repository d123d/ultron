"""Git worktree isolation so parallel workers never share a checkout."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .procs import FleetError

_ID = ["-c", "user.name=ai-fleet", "-c", "user.email=ai-fleet@localhost"]


def _git(cwd: Path, *args: str, check: bool = True) -> str:
    p = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)
    if check and p.returncode != 0:
        raise FleetError(f"git {' '.join(args)} failed: {p.stderr.strip()[:300]}")
    return p.stdout.strip()


def _exclude(root: Path, extra: list[str] | None = None) -> None:
    exclude = Path(_git(root, "rev-parse", "--git-path", "info/exclude"))
    if not exclude.is_absolute():
        exclude = root / exclude
    exclude.parent.mkdir(parents=True, exist_ok=True)
    text = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    lines = text.splitlines()
    add = [e for e in [".fleet/", *(extra or [])] if e not in lines]
    if add:
        exclude.write_text(text + ("" if text.endswith("\n") or not text else "\n") + "\n".join(add) + "\n", encoding="utf-8")


def create(cwd: str | Path, run_id: str) -> tuple[Path, Path, str]:
    """Return (repo_root, worktree_cwd, branch). worktree_cwd keeps cwd's position relative to the repo root."""
    cwd = Path(cwd).resolve()
    root = Path(_git(cwd, "rev-parse", "--show-toplevel")).resolve()
    rel = cwd.relative_to(root)
    _exclude(root)
    path = root / ".fleet" / "worktrees" / run_id
    branch = f"fleet/{run_id}"
    _git(root, "worktree", "add", "-b", branch, str(path), "HEAD")
    linked = link_ignored(root, path)
    if linked:
        # A POSIX symlink to a dir does not match dir-only ignore patterns (a Windows
        # junction does, being a real dir) — exclude linked names explicitly so
        # commit_all can never commit a link into the branch.
        _exclude(root, [e.rstrip("/") for e in linked])
    return root, path / rel, branch


_SKIP_PARTS = {".fleet", ".git", ".claude", ".ai-locks", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache", ".cache"}


def ignored_entries(root: Path) -> list[str]:
    """Git-ignored paths of the main checkout (dirs end with '/'), minus caches, locks and other worktrees."""
    out = _git(root, "ls-files", "--others", "--ignored", "--exclude-standard", "--directory", check=False)
    return [e for e in (line.strip() for line in out.splitlines())
            if e and not _SKIP_PARTS.intersection(e.rstrip("/").split("/"))]


def _link(src: Path, dst: Path) -> bool:
    try:
        if src.is_dir():
            try:
                import _winapi  # Windows junction: no admin rights needed, unlike symlinks

                _winapi.CreateJunction(str(src), str(dst))
            except ImportError:
                os.symlink(src, dst, target_is_directory=True)  # POSIX: symlink is the junction equivalent
        else:
            os.link(src, dst)
        return True
    except (OSError, ImportError):
        return False


def link_ignored(root: Path, path: Path) -> list[str]:
    """Link git-ignored assets (venvs, renders, reference images) into a new worktree so done_when checks see the
    same files as the main tree. Dirs become junctions, files hard links; both stay ignored by the tracked .gitignore."""
    linked = []
    for entry in ignored_entries(root):
        rel = entry.rstrip("/")
        src, dst = root / rel, path / rel
        if dst.exists() or not src.exists() or not dst.parent.is_dir():
            continue
        if _link(src, dst):
            linked.append(rel)
    return linked


def unlink_ignored(path: Path) -> int:
    """Remove the junctions and hard links link_ignored made, so deleting the worktree can never reach main-tree assets.
    Removing a junction or one hard link leaves the target data untouched."""
    n = 0
    for entry in ignored_entries(path):
        p = path / entry.rstrip("/")
        try:
            if p.is_junction() or p.is_symlink():
                try:
                    os.rmdir(p)
                except OSError:
                    os.unlink(p)
                n += 1
            elif p.is_file() and os.stat(p).st_nlink > 1:
                os.unlink(p)
                n += 1
        except OSError:
            pass
    return n


def commit_all(worktree: Path, message: str) -> str | None:
    if not _git(worktree, "status", "--porcelain"):
        return None
    _git(worktree, "add", "-A")
    _git(worktree, *_ID, "commit", "-q", "-m", message)
    return _git(worktree, "rev-parse", "HEAD")


def list_worktrees(cwd: str | Path) -> list[dict[str, str]]:
    out = _git(Path(cwd), "worktree", "list", "--porcelain")
    items, cur = [], {}
    for line in out.splitlines() + [""]:
        if not line:
            if cur:
                items.append(cur)
            cur = {}
            continue
        k, _, v = line.partition(" ")
        cur[k] = v
    return [i for i in items if "/.fleet/worktrees/" in i.get("worktree", "").replace("\\", "/")]


def prune_merged(cwd: str | Path) -> list[str]:
    root = Path(_git(Path(cwd), "rev-parse", "--show-toplevel"))
    merged = {b.strip().lstrip("*+ ").strip() for b in _git(root, "branch", "--merged", "HEAD").splitlines()}
    removed = []
    for wt in list_worktrees(root):
        branch = wt.get("branch", "").removeprefix("refs/heads/")
        if branch in merged:
            unlink_ignored(Path(wt["worktree"]))
            _git(root, "worktree", "remove", wt["worktree"])
            _git(root, "branch", "-d", branch)
            removed.append(branch)
    return removed
