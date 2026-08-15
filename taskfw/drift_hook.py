"""PostToolUse hook — the awareness nudge, invoked directly by Claude Code.

task:8be768df. taskfw's drift-reflection nudge used to trigger only from
taskfw's own MCP tool calls (mcp_server.py's now-removed _drift_reflection_hook),
so a stretch of Bash/Read/Write/Edit turns with no taskfw tool call never
advanced it and the nudge could go silent for arbitrarily long stretches.
claude-hooks' PostToolUse hook sees every tool call in the session, but
routing the nudge through claude-hooks was rejected — task-framework is the
sole owner of active-task state (task:f1d46386's tombstone in
claude-hooks/hooks/dispatcher.py says so explicitly), and claude-hooks has no
shared install with taskfw's own venv to import it directly.

Claude Code's settings.json PostToolUse array already runs more than one
independent hook entry on the same event (claude-hooks' own client.py and
this repo's concept_store/diff_hook.py coexist there) — so this module
registers as one more, invoked as a subprocess in taskfw's own venv, with no
relay through claude-hooks and no new daemon. Same shape as taskfw/backfill.py:
a plain argparse main(), no dependency on the mcp package.

Stateless by design — this runs as a fresh subprocess on every call, so it
cannot hold a call counter itself. The every-Nth-call throttle (task:1c8f0815)
is instead driven by a counter claude-hooks/dispatcher.py maintains in its own
long-running process (one per session, unlike this subprocess) and passes in
as "_taskfw_drift_call_count" on the same stdin payload; the gating decision
itself still lives here, in dispatcher.drift_reflection_nudge, keeping the
active-task/drift logic solely owned by taskfw even though the raw count is
sourced elsewhere. Absent that field (e.g. a manual/non-claude-hooks caller),
the nudge fires on every call, same as before this change.
"""
from __future__ import annotations

import json
import os
import sys

from taskfw import dispatcher
from taskfw.dispatcher import next_open_item, phase_label, task_phase
from taskfw.log import get_logger
from taskfw.store import TaskStore

log = get_logger(__name__)


def _scope() -> str:
    """Mirrors mcp_server._scope(): per-workspace when set, else cwd."""
    return os.environ.get("TASKFW_SCOPE") or os.getcwd()


def build_nudge(store: TaskStore, scope: str, call_count: int | None) -> str | None:
    active_task_id = store.get_active(scope) or ""
    if not active_task_id:
        log.debug("drift nudge skipped: no active task for scope=%s", scope)
        return None
    active_task = store.get(active_task_id)
    if active_task is None:
        log.debug("drift nudge skipped: active_task_id=%s not found in store", active_task_id)
        return None
    nudge = dispatcher.drift_reflection_nudge(
        active_task_id, active_task.title, phase_label(task_phase(active_task)),
        next_open_item(active_task) or "", call_count,
    )
    # info, not debug: get_logger() (taskfw/log.py) pins the root "taskfw"
    # logger at config.LOG_LEVEL, INFO by default, so a debug call here would
    # never reach the log store. Kept scoped to the has-an-active-task path
    # (unlike the two debug calls above) since this hook runs on every
    # PostToolUse call system-wide — logging the far more common no-active-
    # task case at info would flood the store with steady-state noise that
    # was never the throttle behavior worth observing.
    if nudge is None:
        log.info("drift nudge skipped: throttled, task=%s call_count=%s", active_task_id, call_count)
    else:
        log.info("drift nudge fired: task=%s call_count=%s", active_task_id, call_count)
    return nudge


def main(argv: list[str] | None = None) -> int:
    """Reads a PostToolUse hook payload from stdin, writes hookSpecificOutput to stdout.

    Fails open on any error — a hook that crashes or emits garbage must never
    block the tool call it's attached to. Silence (empty stdout) is exactly
    how Claude Code reads "nothing to add" for a PostToolUse hook.
    """
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        payload = {}

    call_count = payload.get("_taskfw_drift_call_count")
    if not isinstance(call_count, int):
        call_count = None

    try:
        nudge = build_nudge(TaskStore(), _scope(), call_count)
    except Exception:
        log.exception("drift_hook failed")
        return 0

    if nudge:
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": nudge,
            },
        }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
