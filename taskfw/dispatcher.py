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


_DRIFT_REFLECTION_INTERVAL = 4  # calls between nudges, per (scope, task) — tightened from claude-hooks' original 8 (task:a6fb9f45)
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

    AN AWARENESS NUDGE, NOT A DETECTION MECHANISM. This function has no view
    into what happened during the calls it's counting — it cannot tell drift
    from a stretch of perfectly on-task work, because it isn't given anything
    to compare against. What it does is force a periodic re-read of the task
    label, on a fixed interval, so awareness has to be re-established rather
    than fading silently after the one-time announcement at activation.
    Whether that re-established awareness catches anything is left entirely
    to whoever reads it; the mechanism's job ends at making the check-in
    happen, not at judging its outcome.
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


def is_implemented(task) -> bool:
    """Whether task's resolution checklist is complete — status-independent.

    The pure predicate, extracted so finish_reminder_nudge and task_phase
    share one place computing "100% done" instead of two copies that could
    drift. Deliberately excludes finish_reminder_nudge's own status=='open'
    gate: that gate is about whether to nudge someone to call tasks__finish
    (pointless once the task is already done), not about whether the work
    itself is complete — a done task with a full checklist is still
    implemented.
    """
    done, total = task.progress
    return total > 0 and done == total


def finish_reminder_nudge(task) -> str | None:
    """Advisory nudge for tasks__check_item/tasks__update, or None when there's nothing to say.

    Stateless by design, unlike drift_reflection_nudge's call counter: this
    checks the task's current resolution/status on every qualifying save
    rather than firing once and remembering it fired, because check_item and
    update are called far less often per task than the mutating tools
    drift_reflection_nudge throttles, so re-firing on a later save that still
    finds the task 100%-done-but-open costs little and needs no extra state.

    A checklist reaching 4/4 is not the same as the task being finished
    (task:a6fb9f45 sat open at 4/4 until an unrelated review pass caught it,
    since nothing surfaced the gap) — this closes that gap for whoever is
    looking at the result.
    """
    if not is_implemented(task) or task.status != "open":
        return None
    _, total = task.progress
    return f"task:{task.id} has all {total} resolution item(s) checked but is still open. Call tasks__finish when the work is done."


def task_phase(task) -> dict[str, bool]:
    """Where a task stands in the grooming -> implementation -> introspection loop.

    Fully derived from fields the task already carries — never a stored
    status. Folding this into a new column or into active_task (a scope ->
    task_id pointer, not a per-task field) would duplicate a rule this
    module and lifecycle.py already own; see task:bf95ced8's grooming for
    why that was rejected. A task is "groomed" when its grooming findings
    are non-empty (there is no separate groomed_at flag — same reasoning as
    task-grooming/skill.md's "a task is groomed when its grooming is
    non-empty"), "implemented" per is_implemented above, and "introspected"
    when at least one introspection report has been appended.
    """
    return {
        "groomed": bool(task.grooming),
        "implemented": is_implemented(task),
        "introspected": bool(task.introspection),
    }


def apply_nudge(result: dict[str, Any], key: str, nudge: str | None) -> None:
    """Set `result[key] = nudge`, or leave `result` untouched when there's nothing to say.

    One shared wrapper for every nudge function's result — call the nudge
    function yourself first (its own signature is the real variation between
    introspection_nudge/drift_reflection_nudge/finish_nudge/
    finish_reminder_nudge; there is nothing left to template once the "if
    truthy, set the key" step is factored out).
    """
    if nudge:
        result[key] = nudge


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

    Call sites build `post` with a lambda, not `functools.partial`: `apply_nudge`
    takes `result` first, but `partial` appends new positional args after the
    ones it pre-binds, so `partial(apply_nudge, key, nudge)` called as
    `p(result)` would call `apply_nudge(key, nudge, result)` — the wrong
    order. A lambda reads left-to-right in the same order as the function
    signature it calls; `partial` would need every pre-bound argument passed
    by keyword to avoid that, for no benefit at a single call site.
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
