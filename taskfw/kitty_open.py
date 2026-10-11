"""Open a task in a kitty tab by shelling out to `kitty-taskfw open` (task:87c91a76).

Optional by construction: nothing here imports kitty-taskfw, and nothing runs
at import time. The command is looked up inside the call, so an absent
kitty-taskfw is a soft `not_opened` result, never an exception.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from typing import Any, Callable

#: Env override: a shell-split argv that replaces `kitty-taskfw`.
CMD_ENV = "TASKFW_KITTY_OPEN_CMD"
#: The CLI makes several kitten calls of up to 20 s each; stay above one call.
TIMEOUT_S = 60

_STATUSES = ("opened", "exists", "not_opened")


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
    found = shutil.which("kitty-taskfw", path=env.get("PATH"))
    if not found:
        return None, f"kitty-taskfw not found (set {CMD_ENV} or put it on PATH)"
    return [found], ""


def open_task(
    task_id: str,
    worktree: str,
    runner: Callable[..., Any] = subprocess.run,
    env: dict[str, str] | None = None,
    timeout: float = TIMEOUT_S,
) -> dict[str, Any]:
    """Run `kitty-taskfw open <task_id> --worktree <worktree>`; never raises.

    Returns {status, window_id, socket, reason}, status one of
    opened / exists / not_opened. `runner` is injectable so tests never fork.
    """
    env = dict(os.environ if env is None else env)
    cmd, why_not = _command(env)
    if cmd is None:
        return _not_opened(why_not)
    argv = [*cmd, "open", "--worktree", worktree, task_id]
    try:
        proc = runner(argv, capture_output=True, text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return _not_opened(
            f"kitty-taskfw open timed out after {timeout:g}s; the tab may still have "
            "launched, retry reports `exists` if so"
        )
    except Exception as e:  # missing executable, permission, anything else
        return _not_opened(f"could not run kitty-taskfw: {type(e).__name__}: {e}")
    try:
        data = json.loads(proc.stdout)
    except (TypeError, ValueError):
        err = (proc.stderr or "").strip().splitlines()
        tail = f": {err[-1]}" if err else ""
        return _not_opened(f"unparseable kitty-taskfw output (exit {proc.returncode}){tail}")
    if not isinstance(data, dict) or data.get("status") not in _STATUSES:
        return _not_opened("unexpected kitty-taskfw output: no recognised status")
    return {
        "status": data["status"],
        "window_id": data.get("window_id"),
        "socket": data.get("socket"),
        "reason": data.get("reason"),
    }
