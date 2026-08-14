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

Stateless by design (dispatcher.drift_reflection_nudge takes no counter) —
this runs as a fresh subprocess on every call, so there is no cross-process
state to keep in sync in the first place.
"""
from __future__ import annotations

import json
import os
import sys

from taskfw import dispatcher
from taskfw.dispatcher import phase_label, task_phase
from taskfw.log import get_logger
from taskfw.store import TaskStore

log = get_logger(__name__)


def _scope() -> str:
    """Mirrors mcp_server._scope(): per-workspace when set, else cwd."""
    return os.environ.get("TASKFW_SCOPE") or os.getcwd()


def build_nudge(store: TaskStore, scope: str) -> str | None:
    active_task_id = store.get_active(scope) or ""
    if not active_task_id:
        return None
    active_task = store.get(active_task_id)
    if active_task is None:
        return None
    return dispatcher.drift_reflection_nudge(
        active_task_id, active_task.title, phase_label(task_phase(active_task)),
    )


def main(argv: list[str] | None = None) -> int:
    """Reads a PostToolUse hook payload from stdin, writes hookSpecificOutput to stdout.

    Fails open on any error — a hook that crashes or emits garbage must never
    block the tool call it's attached to. Silence (empty stdout) is exactly
    how Claude Code reads "nothing to add" for a PostToolUse hook.
    """
    try:
        json.load(sys.stdin)  # payload isn't otherwise needed; scope/task come from taskfw's own store
    except (json.JSONDecodeError, ValueError):
        return 0

    try:
        nudge = build_nudge(TaskStore(), _scope())
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
