"""Link a commit to its task the moment Claude Code makes it (task:dd2cf1ec).

A Claude Code PostToolUse command hook for the Bash tool, registered in the
global ~/.claude/settings.json:

    {"matcher": "Bash", "hooks": [{"type": "command",
      "command": "<task-framework>/.venv/bin/python -m taskfw.commit_hook"}]}

When the Bash command ran `git commit`, it reads the new HEAD of that repo and
links every task:<id> in HEAD's message to HEAD's sha — the same derivation
taskfw.backfill uses (extract_task_ids + TaskStore.add_commit), applied to one
commit. Before this, the link existed only if someone remembered to call
tasks__add_commit afterwards, and three tasks shipped with none.

Why here and not elsewhere (the decision on task:dd2cf1ec): claude-hooks sees
every commit but was deliberately decoupled from taskfw's store, and a global
git core.hooksPath would switch off the per-repo hooks some repos already have.
A hook owned by taskfw and calling its own store keeps both intact. The gap it
accepts: a commit typed by hand in a terminal is not seen. Backfill
(`python -m taskfw.backfill`) and task-introspection's git-log diff still
recover that.

A failed commit links nothing new. A PostToolUse hook has no "HEAD before",
so freshness stands in for success: HEAD is linked only when its committer
date is within FRESH_SECONDS. Git stamps that date after pre-commit hooks
run, so a slow pre-commit suite does not push a commit out of the window.

Limits, accepted because backfill recovers them: the command parse follows
`cd <dir>` and `git -C <dir>` only, not subshells, pushd, `bash -c` or $(...).

Fail open: any error is logged and swallowed, the process prints nothing and
exits 0. A hook that breaks would sit between the user and every Bash call.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import sys
import time
from typing import IO

from taskfw.backfill import extract_task_ids
from taskfw.gitutil import run_git
from taskfw.log import get_logger

log = get_logger(__name__)

#: How recent HEAD's commit must be to count as the one this command just made.
FRESH_SECONDS = 120

#: Shell operators that separate one command from the next.
_SEGMENT_SPLIT = re.compile(r"&&|\|\||;|\||\n")

#: git options that take a value as the next argument.
_GIT_VALUE_FLAGS = {"-C", "-c", "--git-dir", "--work-tree", "--namespace"}

_ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def _tokens(segment: str) -> list[str]:
    try:
        return shlex.split(segment, comments=True)
    except ValueError:  # heredocs and $(...) leave quotes unbalanced
        return segment.split()


def _expand(path: str, cwd: str) -> str:
    path = os.path.expanduser(os.path.expandvars(path))
    return os.path.normpath(os.path.join(cwd, path))


def commit_dirs(command: str, cwd: str) -> list[str]:
    """Directories in which `command` runs `git commit`, in order, de-duplicated.

    Follows `cd <dir>` segments and `git -C <dir>`, because the hook's cwd is
    the session's directory and a command can commit somewhere else. Detection
    is on the command word, so `echo "git commit"` is not a commit.
    """
    here, found = cwd, []
    for segment in _SEGMENT_SPLIT.split(command):
        toks = _tokens(segment)
        i = 0
        while i < len(toks) and _ENV_ASSIGN.match(toks[i]):
            i += 1
        if i >= len(toks):
            continue
        word = toks[i].rsplit("/", 1)[-1]
        if word == "cd" and i + 1 < len(toks):
            here = _expand(toks[i + 1], here)
            continue
        if word != "git":
            continue
        repo, j = here, i + 1
        while j < len(toks):
            tok = toks[j]
            if tok in _GIT_VALUE_FLAGS:
                if tok == "-C" and j + 1 < len(toks):
                    repo = _expand(toks[j + 1], repo)
                j += 2
            elif tok.startswith("-"):
                j += 1
            else:
                if tok == "commit" and repo not in found:
                    found.append(repo)
                break
    return found


def head_commit(repo_dir: str) -> tuple[str, str, str, int] | None:
    """(toplevel, sha, message, committer epoch) of HEAD, or None outside a repo."""
    top = run_git(["rev-parse", "--show-toplevel"], cwd=repo_dir)
    if top is None or top.returncode != 0:
        return None
    toplevel = top.stdout.strip()
    out = run_git(["log", "-1", "--format=%H%x1f%ct%x1f%B"], cwd=toplevel)
    if out is None or out.returncode != 0:
        return None
    sha, ct, message = out.stdout.split("\x1f", 2)
    return toplevel, sha.strip(), message, int(ct.strip())


def link_head(repo_dir: str, store=None, now: float | None = None) -> list[str]:
    """Link HEAD of repo_dir to each existing task its message cites; return the ids linked."""
    head = head_commit(repo_dir)
    if head is None:
        return []
    toplevel, sha, message, committed_at = head
    if (now if now is not None else time.time()) - committed_at > FRESH_SECONDS:
        return []
    ids = extract_task_ids(message)
    if not ids:
        return []
    if store is None:
        from taskfw.store import TaskStore  # deferred: most Bash calls never get here
        store = TaskStore()
    linked = []
    for task_id in ids:
        if store.get(task_id) is None:
            log.info("commit_hook: %s cites unknown task %s", sha[:12], task_id)
            continue
        store.add_commit(task_id, sha, toplevel)
        linked.append(task_id)
    return linked


def handle(payload: dict, store=None, now: float | None = None) -> list[str]:
    """Every task id linked for this hook event (empty when it was not a git commit)."""
    if payload.get("tool_name") != "Bash":
        return []
    command = (payload.get("tool_input") or {}).get("command") or ""
    if "commit" not in command or "git" not in command:
        return []
    cwd = payload.get("cwd") or os.getcwd()
    linked: list[str] = []
    for repo_dir in commit_dirs(command, cwd):
        linked += link_head(repo_dir, store=store, now=now)
    return linked


def main(stdin: IO[str] = sys.stdin) -> int:
    try:
        handle(json.load(stdin))
    except Exception:  # noqa: BLE001 — fail open, never block a Bash call
        log.exception("commit_hook failed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
