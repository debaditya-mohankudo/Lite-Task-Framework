"""Open a task in a kitty tab by shelling out to `python -m taskfw.kitty.launcher open`
(task:87c91a76, task:3ddbfc92).

Optional by construction: nothing here imports taskfw.kitty, and nothing runs
at import time. The launcher runs in a child process, so a broken kitty setup
is a soft `not_opened` result, never an exception in the MCP server.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import unicodedata
from typing import Any, Callable

#: Env override: a shell-split argv that replaces `python -m taskfw.kitty.launcher`.
CMD_ENV = "TASKFW_KITTY_OPEN_CMD"
#: The CLI makes several kitten calls of up to 20 s each; stay above one call.
TIMEOUT_S = 60

_STATUSES = ("opened", "exists", "not_opened")


def bad_parent(parent: str) -> str | None:
    """Why `parent` cannot go into a prompt, or None. It must be one line with no control or line-separator
    characters (Cc covers \\n, \\r and U+0085; Zl and Zp are U+2028 and U+2029)."""
    if parent and (not parent.strip() or any(unicodedata.category(c) in ("Cc", "Zl", "Zp") for c in parent)):
        return "parent must be a single line with no control characters"
    return None


def report_back(parent: str, what: str, when: str = "When finished") -> str:
    """The one sentence that tells a new tab to report to `parent` via SendMessage: the single home for
    this wording, shared by the task tab's default prompt and `launcher plain --parent`."""
    return (f"{when} call SendMessage to {parent} with {what}; never the brief. "
            "If SendMessage fails, say so in your final output and stop.")


def _not_opened(reason: str) -> dict[str, Any]:
    return {"status": "not_opened", "window_id": None, "socket": None, "reason": reason}


def _command(env: dict[str, str]) -> tuple[list[str] | None, str]:
    """(argv, "") when a command was found, else (None, why not).

    A set-but-unusable override gets its own reason: reporting it as "not
    found" would send the user to PATH when the problem is the env var.
    """
    override = env.get(CMD_ENV, "").strip()
    if override:
        try:
            argv = shlex.split(override)
        except ValueError as e:
            return None, f"{CMD_ENV} is set but unparseable ({e}): {override!r}"
        if not argv or not argv[0]:  # "''" splits to [''], not []
            return None, f"{CMD_ENV} is set but names no command: {override!r}"
        return argv, ""
    return [sys.executable, "-m", "taskfw.kitty.launcher"], ""


def open_task(
    task_id: str,
    worktree: str,
    runner: Callable[..., Any] = subprocess.run,
    env: dict[str, str] | None = None,
    timeout: float = TIMEOUT_S,
    prompt: str = "",
    isolate: bool | None = None,
) -> dict[str, Any]:
    """Run `taskfw.kitty.launcher open --worktree <worktree> <task_id> [--prompt P] [--isolate|--no-isolate]`; never raises.

    Returns {status, window_id, socket, reason, worktree, isolated}, status one of
    opened / exists / not_opened. `runner` is injectable so tests never fork.
    """
    env = dict(os.environ if env is None else env)
    cmd, why_not = _command(env)
    if cmd is None:
        return _not_opened(why_not)
    argv = [*cmd, "open", "--worktree", worktree, task_id]
    if prompt:  # one argv element: the launcher hands it to claude as its initial argument
        argv += ["--prompt", prompt]
    if isolate is not None:  # unset leaves the launcher to decide from live sibling tabs
        argv.append("--isolate" if isolate else "--no-isolate")
    try:
        proc = runner(
            argv, capture_output=True, text=True, timeout=timeout, env=env,
            stdin=subprocess.DEVNULL,  # never share the MCP server's stdio stream
        )
    except subprocess.TimeoutExpired:
        return _not_opened(
            f"kitty launcher open timed out after {timeout:g}s; the tab may still have "
            "launched, retry reports `exists` if so"
        )
    except Exception as e:  # missing executable, permission, anything else
        return _not_opened(f"could not run the kitty launcher: {type(e).__name__}: {e}")
    try:
        data = json.loads(proc.stdout)
    except (TypeError, ValueError):
        err = (proc.stderr or "").strip().splitlines()
        tail = f": {err[-1]}" if err else ""
        return _not_opened(f"unparseable kitty launcher output (exit {proc.returncode}){tail}")
    if not isinstance(data, dict) or data.get("status") not in _STATUSES:
        return _not_opened("unexpected kitty launcher output: no recognised status")
    return {
        "status": data["status"],
        "window_id": data.get("window_id"),
        "socket": data.get("socket"),
        "reason": data.get("reason"),
        "worktree": data.get("worktree"),
        "isolated": data.get("isolated"),
    }
