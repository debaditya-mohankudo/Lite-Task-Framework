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


def _run_launcher(argv: list[str], runner: Callable[..., Any], env: dict[str, str], timeout: float,
                  sub: str, retry_hint: str = "") -> dict[str, Any]:
    """Run the launcher child and return its parsed JSON; when it cannot run or its output is unusable,
    a not_opened dict carrying `_failed: True` so the caller returns it as is. Never raises."""
    def failed(reason: str) -> dict[str, Any]:
        return {**_not_opened(reason), "_failed": True}
    try:
        proc = runner(
            argv, capture_output=True, text=True, timeout=timeout, env=env,
            stdin=subprocess.DEVNULL,  # never share the MCP server's stdio stream
        )
    except subprocess.TimeoutExpired:
        return failed(f"kitty launcher {sub} timed out after {timeout:g}s; the tab may still have launched"
                      + retry_hint)
    except Exception as e:  # missing executable, permission, anything else
        return failed(f"could not run the kitty launcher: {type(e).__name__}: {e}")
    try:
        data = json.loads(proc.stdout)
    except (TypeError, ValueError):
        err = (proc.stderr or "").strip().splitlines()
        tail = f": {err[-1]}" if err else ""
        return failed(f"unparseable kitty launcher output (exit {proc.returncode}){tail}")
    if not isinstance(data, dict) or data.get("status") not in _STATUSES:
        return failed("unexpected kitty launcher output: no recognised status")
    return data


def open_task(
    task_id: str,
    worktree: str,
    runner: Callable[..., Any] = subprocess.run,
    env: dict[str, str] | None = None,
    timeout: float = TIMEOUT_S,
    prompt: str = "",
    isolate: bool | None = None,
    model: str = "",
    model_reason: str = "",
) -> dict[str, Any]:
    """Run `taskfw.kitty.launcher open --worktree <worktree> <task_id> [--prompt P] [--isolate|--no-isolate] [--model M [--model-reason R]]`; never raises.

    Returns {status, window_id, socket, reason, worktree, isolated, model, model_source}, status one of
    opened / exists / not_opened. `runner` is injectable so tests never fork.
    """
    env = dict(os.environ if env is None else env)
    cmd, why_not = _command(env)
    if cmd is None:
        return _not_opened(why_not)
    argv = [*cmd, "open", "--worktree", worktree, task_id]
    if prompt:  # one argv element: the launcher hands it to claude as its initial argument
        argv += ["--prompt", prompt]
    if model:  # an explicit override; unset leaves the launcher to choose from the task
        argv += ["--model", model]
        if model_reason:
            argv += ["--model-reason", model_reason]
    if isolate is not None:  # unset leaves the launcher to decide from live sibling tabs
        argv.append("--isolate" if isolate else "--no-isolate")
    data = _run_launcher(argv, runner, env, timeout, "open", "; retry reports `exists` if so")
    if data.pop("_failed", False):
        return data
    return {
        "status": data["status"],
        "window_id": data.get("window_id"),
        "socket": data.get("socket"),
        "reason": data.get("reason"),
        "worktree": data.get("worktree"),
        "isolated": data.get("isolated"),
        "model": data.get("model"),
        "model_source": data.get("model_source"),
    }


def open_plain_tab(
    cwd: str,
    parent: str,
    prompt: str = "",
    title: str = "",
    model: str = "",
    model_reason: str = "",
    max_life_minutes: int | None = None,
    runner: Callable[..., Any] = subprocess.run,
    env: dict[str, str] | None = None,
    timeout: float = TIMEOUT_S,
) -> dict[str, Any]:
    """Run `taskfw.kitty.launcher open-plain --cwd C --parent P [...]`; never raises.

    A task-less tab: no task row is created, so there is nothing to purge or exclude from readers.
    Returns {status, window_id, socket, reason, model, model_source}; status is opened or not_opened.
    """
    env = dict(os.environ if env is None else env)
    cmd, why_not = _command(env)
    if cmd is None:
        return _not_opened(why_not)
    argv = [*cmd, "open-plain", "--cwd", cwd, "--parent", parent]
    if prompt:
        argv += ["--prompt", prompt]
    if title:
        argv += ["--title", title]
    if model:
        argv += ["--model", model]
        if model_reason:
            argv += ["--model-reason", model_reason]
    if max_life_minutes is not None:
        argv += ["--max-life-minutes", str(max_life_minutes)]
    data = _run_launcher(argv, runner, env, timeout, "open-plain")
    if data.pop("_failed", False):
        return data
    return {key: data.get(key) for key in ("status", "window_id", "socket", "reason", "model", "model_source")}
