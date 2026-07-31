"""Claude Code hooks — the only host-specific surface in the framework.

Optional by design. Everything the framework does is reachable through MCP
tools; these hooks only cover the two things a pull interface structurally
cannot do. A host without hook support loses automaticity, nothing else.

Imports taskfw directly — there is no server to reach, so there is no
transport, no timeout, and no unreachable-server case.

TWO BEHAVIOURS, independently switchable. Commit capture is useful to someone
who wants no injection at all, so coupling them would force an all-or-nothing
adoption decision:

  1. UserPromptSubmit -> a ONE-LINE active-task pointer. Not an assembled
     context block: the bundle is a pull (tasks__context), and the hook only
     has to make the agent aware there is something to pull.

     This IS prompt injection, just one line of it. Saying so plainly matters
     more than defending the phrase "pull only" on a technicality.

  2. PostToolUse -> commit capture. A Bash `git commit` is a tool the framework
     does not own, so the commit-to-task link can only be recorded by observing
     it afterwards. Pure observation: nothing is blocked, ever.

FAIL-OPEN IS ABSOLUTE. Any exception is swallowed and an empty result returned.
With no enforcement left in the hook layer, the worst case is a missing pointer
or an unrecorded commit — never a blocked tool.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from typing import Any

from taskfw.log import get_logger
from taskfw.store import TaskStore

log = get_logger(__name__)

#: Matches task:<id> anywhere in a commit message.
TASK_REF = re.compile(r"\btask:([0-9a-f]{6,32})\b", re.IGNORECASE)

#: Recognises a commit command without trying to parse shell grammar.
GIT_COMMIT = re.compile(r"\bgit\s+(?:-[^\s]+\s+)*commit\b")


def _enabled(flag: str) -> bool:
    """Both behaviours default ON, and either can be disabled independently."""
    return os.environ.get(flag, "1").lower() not in ("0", "false", "no")


def extract_task_ids(text: str) -> list[str]:
    """Every task:<id> reference in a string, de-duplicated, order preserved."""
    seen: dict[str, None] = {}
    for match in TASK_REF.finditer(text or ""):
        seen.setdefault(match.group(1).lower(), None)
    return list(seen)


def _scope(payload: dict) -> str:
    """Active-task scope.

    Uses the cwd the host supplied and never overwrites it with an env
    fallback — doing so once made every session report the launching process's
    directory instead of its own.
    """
    return payload.get("cwd") or os.environ.get("TASKFW_SCOPE") or os.getcwd()


# ---------------------------------------------------------------------------
# UserPromptSubmit — the pointer
# ---------------------------------------------------------------------------

def handle_user_prompt_submit(payload: dict, store: TaskStore | None = None) -> dict[str, Any]:
    """Inject one line naming the active task, or nothing at all."""
    if not _enabled("TASKFW_HOOK_POINTER"):
        return {}
    s = store or TaskStore()
    scope = _scope(payload)
    task_id = s.get_active(scope)
    if not task_id:
        log.debug("pointer: no active task scope=%s", scope)
        return {}
    task = s.get(task_id)
    if task is None:
        # The pointer outlived its task; clear it rather than name a ghost.
        log.info("pointer: active task=%s no longer exists, clearing scope=%s", task_id, scope)
        s.clear_active(scope)
        return {}
    done, total = task.progress
    line = (
        f"active task: {task.id} — {task.title} "
        f"[{task.status}, {done}/{total} done]. Call tasks__context for details."
    )
    log.info("pointer: task=%s scope=%s", task.id, scope)
    return {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": line,
        }
    }


# ---------------------------------------------------------------------------
# PostToolUse — commit capture
# ---------------------------------------------------------------------------

def handle_post_tool_use(payload: dict, store: TaskStore | None = None) -> dict[str, Any]:
    """Record a commit against every task its message references."""
    if not _enabled("TASKFW_HOOK_COMMITS"):
        return {}
    if payload.get("tool_name") != "Bash":
        return {}
    command = (payload.get("tool_input") or {}).get("command", "")
    if not GIT_COMMIT.search(command):
        return {}

    task_ids = extract_task_ids(command)
    if not task_ids:
        # No enforcement here by design — an untagged commit is simply not
        # linked. `taskfw-backfill` recovers these from git history.
        log.info("commit capture: no task:<id> in commit command, nothing to link")
        return {}

    cwd = payload.get("cwd") or os.getcwd()
    sha = _head_sha(cwd)
    if not sha:
        return {}

    s = store or TaskStore()
    for task_id in task_ids:
        if s.get(task_id) is None:
            log.info("commit capture: task=%s referenced but does not exist", task_id)
            continue
        s.add_commit(task_id, sha, cwd)
    return {}


def _head_sha(cwd: str) -> str:
    """Resolve HEAD. Returns "" when cwd is not a git repo, which is not an error."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=cwd, capture_output=True, text=True, timeout=5
        )
        if out.returncode != 0:
            log.debug("commit capture: git rev-parse failed in %s", cwd)
            return ""
        return out.stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("commit capture: could not resolve HEAD (%s)", exc)
        return ""


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

HANDLERS = {
    "UserPromptSubmit": handle_user_prompt_submit,
    "PostToolUse": handle_post_tool_use,
}


def main(argv: list[str] | None = None) -> int:
    """Read a hook payload on stdin, write the result as JSON on stdout.

    Always exits 0 and always prints valid JSON, whatever happens. A hook that
    fails must never be the reason a tool call is blocked.
    """
    argv = argv if argv is not None else sys.argv[1:]
    event = argv[0] if argv else ""
    try:
        payload = json.load(sys.stdin)
        handler = HANDLERS.get(event)
        result = handler(payload) if handler else {}
    except Exception as exc:  # noqa: BLE001 — fail-open is the whole point
        log.warning("hook %s failed open: %s", event or "?", exc)
        result = {}
    print(json.dumps(result or {}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
