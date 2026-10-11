"""Git worktree per task. The path and branch are pure functions of (repo, task_id), so two
tasks cannot share a working tree by construction, and a repeated `start` finds its own.

Worktrees live beside the checkout (`<repo>-worktrees/<task_id>`), never inside it, so they
do not show up as untracked files. Removal refuses anything that could lose work.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class WorktreeError(Exception):
    """A worktree could not be created or was refused removal."""


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise WorktreeError(f"git failed to run: {e}") from e


def repo_root(start: str | Path) -> Path | None:
    """The MAIN checkout containing `start` (not a linked worktree), or None if not in a repo."""
    start = Path(start).expanduser()
    if not start.is_dir():
        return None
    p = _git(start, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if p.returncode != 0:
        return None
    common = Path(p.stdout.strip())
    return common.parent if common.name == ".git" else None


def worktree_path(repo: Path, task_id: str) -> Path:
    if not _SAFE_ID.match(task_id):
        raise WorktreeError(f"task id {task_id!r} is not usable in a path or branch name")
    return repo.parent / f"{repo.name}-worktrees" / task_id


def branch_name(task_id: str) -> str:
    return f"task/{task_id}"


def _registered(repo: Path, path: Path) -> bool:
    p = _git(repo, "worktree", "list", "--porcelain")
    return p.returncode == 0 and any(
        line == f"worktree {path}" for line in p.stdout.splitlines())


def ensure(repo: Path, task_id: str) -> Path:
    """Create the task's worktree, or return it if it already exists. Idempotent."""
    path = worktree_path(repo, task_id)
    if _registered(repo, path.resolve() if path.exists() else path):
        return path.resolve()
    if path.exists():
        raise WorktreeError(f"{path} exists but is not a worktree of {repo}; refusing to use it")
    branch = branch_name(task_id)
    have_branch = _git(repo, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}").returncode == 0
    args = ["worktree", "add", str(path)] + ([branch] if have_branch else ["-b", branch])
    path.parent.mkdir(parents=True, exist_ok=True)
    p = _git(repo, *args)
    if p.returncode != 0:
        raise WorktreeError((p.stderr or "git worktree add failed").strip())
    return path.resolve()


def remove(repo: Path, task_id: str) -> str:
    """Remove the task's worktree and branch. Refuses when the tree is dirty or the branch has
    commits the main checkout's HEAD does not contain; returns a one-line description."""
    path = worktree_path(repo, task_id)
    if not path.exists():
        return f"no worktree at {path}"
    st = _git(path, "status", "--porcelain")
    if st.returncode != 0:
        raise WorktreeError(f"cannot read status of {path}: {(st.stderr or '').strip()}")
    if st.stdout.strip():
        raise WorktreeError(f"{path} has uncommitted changes; commit or discard them first")
    branch = branch_name(task_id)
    unmerged = _git(repo, "rev-list", "--count", f"HEAD..{branch}")
    if unmerged.returncode == 0 and unmerged.stdout.strip() != "0":
        raise WorktreeError(f"{branch} has {unmerged.stdout.strip()} commit(s) not in HEAD; merge it first")
    p = _git(repo, "worktree", "remove", str(path))
    if p.returncode != 0:
        raise WorktreeError((p.stderr or "git worktree remove failed").strip())
    _git(repo, "branch", "-d", branch)  # merged (checked above); a failure leaves a harmless branch
    return f"removed {path} and branch {branch}"
