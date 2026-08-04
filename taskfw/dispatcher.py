"""dispatcher — the single home for advisory, non-blocking response annotations.

Distinct from taskfw.lifecycle, which is what may happen (blocking rules,
checked before a write), and taskfw.store, which is how data is persisted.
This is neither: a nudge never refuses a write and never changes what gets
stored, it only tells the caller something true about the result that would
otherwise be silent.

That distinction is the reason this exists as its own module rather than as
inline helpers on whichever MCP tool happens to need one first. mcp_server.py
is meant to stay thin — validate through lifecycle, call store — and a pile of
one-off `_foo_check()` functions attached to individual tools is exactly the
kind of drift that turns "thin" into "thin until the second nudge."

A nudge is advisory because the judgment it points at (does this lesson
generalise beyond this task?) is not a checkable fact. Blocking on it would
only teach callers to write throwaway records to get past the gate — the
underlying defect this module exists to fix is that an omission (nothing
promoted to loop memory) currently looks identical to an absence (nothing to
promote). A nudge makes that difference visible instead of collapsing it.

`tool_called` is the pre/post-hook shape this module's nudges run through.
It is the same shape as claude-hooks' own Bash/MCP gates (task:7b25ee0d
weighed this deliberately before building it) — the difference that made it
worth building here is scope, not kind: it is self-contained inside taskfw's
own server, with no dependency on an external hook process, and it only ever
sees taskfw's own tools, never another server's calls. `post` runs once,
after the block exits with no exception and a result the caller marked `ok`
— a tool that returned an `{"error": ...}` refusal is never nudged.
"""
from __future__ import annotations

import sqlite3
from typing import Any, Callable


def _lesson_texts(report: dict) -> list[str]:
    """Every lesson-shaped entry in an introspection report, across both shapes in use.

    `new_knowledge` (list[str]) is the canonical field — see
    task-introspection/skill.md Step 6, and taskfw.accuracy, which is the only
    other reader of an introspection report and reads exactly this key.
    `surprises[].lesson` is read too, for backward compatibility with reports
    already written in that shape before this module existed (e.g. the six
    tasks under epic 02e7d15e) — reports are append-only history and are never
    rewritten to match a schema that postdates them.
    """
    lessons = [text for text in (report.get("new_knowledge") or []) if text]
    lessons += [
        s["lesson"] for s in (report.get("surprises") or [])
        if isinstance(s, dict) and s.get("lesson")
    ]
    return lessons


def _cited_in_memory(conn: sqlite3.Connection, task_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM memory_links WHERE task_id=? AND relation='learned_from' LIMIT 1",
        (task_id,),
    ).fetchone()
    return row is not None


def introspection_nudge(report: dict, task_id: str, conn: sqlite3.Connection) -> str | None:
    """Advisory nudge for an introspection report, or None when there's nothing to say.

    Fires only when the report carries a lesson AND the task has never cited a
    memory — a task that already promoted one lesson is not nudged again just
    because this report has another; recording is the caller's call, not a
    threshold this function enforces.
    """
    lessons = _lesson_texts(report)
    if not lessons or _cited_in_memory(conn, task_id):
        return None
    return (
        f"{len(lessons)} lesson(s) in this report aren't in loop memory yet. "
        "Call task_memory__record for any that generalize beyond this task."
    )


def apply_introspection_nudge(
    result: dict[str, Any], report: dict, task_id: str, conn: sqlite3.Connection
) -> None:
    """Mutate `result` with a `memory_nudge` key, or leave it untouched."""
    nudge = introspection_nudge(report, task_id, conn)
    if nudge:
        result["memory_nudge"] = nudge


_DRIFT_REFLECTION_INTERVAL = 8  # calls between nudges, per (scope, task) — matches claude-hooks' own interval
_drift_reflection_call_counts: dict[tuple[str, str], int] = {}  # (scope, active_task_id) -> count


def drift_reflection_nudge(scope: str, active_task_id: str, active_task_title: str = "") -> str | None:
    """Port of claude-hooks' _maybe_drift_reflection_nudge (hooks/dispatcher.py), moved
    into task-framework itself.

    That version read active_task_id/title from a claude-hooks session
    checkpoint and fired on Write/Edit/MultiEdit; this one reads the active
    task from taskfw's own store (store().get_active(scope)) and fires on
    taskfw's own mutating tool calls instead — task-framework owns the active
    task now (commit 6633bf1 deleted claude-hooks' langchain_learning task
    nodes on that basis), so this holds regardless of host, and regardless of
    whether claude-hooks' external hook process is even running. A long
    stretch of tool calls can drift from what's active without the caller
    noticing, since the active task is otherwise only ever announced once, at
    activation. No active task means nothing to remind about.
    """
    if not active_task_id:
        return None
    key = (scope, active_task_id)
    count = _drift_reflection_call_counts.get(key, 0) + 1
    _drift_reflection_call_counts[key] = count
    if count % _DRIFT_REFLECTION_INTERVAL != 0:
        return None
    label = f"task:{active_task_id}" + (f" ({active_task_title})" if active_task_title else "")
    return (
        f"Quiet check-in: {count} calls made so far under {label}. Take a moment — "
        "does this stretch of work still match the task's stated intent, or has it "
        "drifted into something adjacent? No need to answer out loud or stop; just "
        "worth noticing before continuing."
    )


def apply_drift_reflection_nudge(
    result: dict[str, Any], scope: str, active_task_id: str, active_task_title: str = ""
) -> None:
    """Mutate `result` with a `drift_reflection_nudge` key, or leave it untouched."""
    nudge = drift_reflection_nudge(scope, active_task_id, active_task_title)
    if nudge:
        result["drift_reflection_nudge"] = nudge


def finish_nudge(task) -> str | None:
    """Advisory nudge for tasks__finish, or None when there's nothing to say.

    Host-agnostic replacement for the reminder claude-hooks' external
    PostToolUse hook currently prints after a task closes — this fires from
    inside taskfw itself, so it holds regardless of host or whether that
    separate hook process happens to be running.
    """
    if task.introspection:
        return None
    return f"task:{task.id} closed with no introspection report yet. Consider running /task-introspection."


def apply_finish_nudge(result: dict[str, Any], task) -> None:
    """Mutate `result` with an `introspection_nudge` key, or leave it untouched."""
    nudge = finish_nudge(task)
    if nudge:
        result["introspection_nudge"] = nudge


def switched_active_nudge(previous_task_id: str | None, task_id: str) -> str | None:
    """Advisory nudge for tasks__set_active, or None when there's nothing to say.

    Fires only when a different task was active a moment ago — re-setting the
    same active task is a no-op and stays silent, the same way
    drift_reflection_nudge does not fire on an idle stretch with no active
    task. The upsert in store.set_active has no memory of what it replaced,
    so this is the one place that difference becomes visible instead of being
    silently clobbered.
    """
    if not previous_task_id or previous_task_id == task_id:
        return None
    return f"Active task switched from task:{previous_task_id} to task:{task_id}."


def apply_switched_active_nudge(
    result: dict[str, Any], previous_task_id: str | None, task_id: str
) -> None:
    """Mutate `result` with an `active_switch_nudge` key, or leave it untouched."""
    nudge = switched_active_nudge(previous_task_id, task_id)
    if nudge:
        result["active_switch_nudge"] = nudge


class tool_called:
    """Pre/post hook around one MCP tool call.

    Usage:

        with dispatcher.tool_called(post=lambda r: ...) as call:
            call.result = {"ok": True, ...}
            return call.result

    `pre` runs on entry. `post` runs on exit, but only when the block raised
    nothing and `call.result` is a dict with a truthy "ok" — a refusal
    ({"error": ...}) is never nudged. Mutating `call.result` in `post` is
    visible in what the function actually returns: `return call.result`
    evaluates the reference before `__exit__` runs as part of unwinding the
    `with` block, and `post` mutates that same dict in place.

    Call sites build `post` with a lambda, not `functools.partial`: the
    `apply_*_nudge` functions take `result` first, but `partial` appends new
    positional args after the ones it pre-binds, so `partial(fn, report,
    task_id, conn)` called as `p(result)` would call `fn(report, task_id,
    conn, result)` — the wrong order. A lambda reads left-to-right in the
    same order as the function signature it calls; `partial` would need
    every pre-bound argument passed by keyword to avoid that, for no benefit
    at a single call site.
    """

    def __init__(
        self,
        pre: Callable[[], None] | None = None,
        post: Callable[[dict[str, Any]], None] | None = None,
    ):
        self.pre = pre
        self.post = post
        self.result: dict[str, Any] = {}

    def __enter__(self) -> "tool_called":
        if self.pre:
            self.pre()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is None and self.post and self.result.get("ok"):
            self.post(self.result)
