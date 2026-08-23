"""The individual advisory nudge functions.

Each is a Callable[[...], str | None] encoding one firing condition, wired
onto a specific MCP tool in mcp_server.py via taskfw.dispatcher.chassis's
tool_called/combine/apply_nudge, and reading task state through
taskfw.dispatcher.phase where relevant.

A nudge is advisory because the judgment it points at (does this lesson
generalise beyond this task?) is not a checkable fact. Blocking on it would
only teach callers to write throwaway records to get past the gate — the
underlying defect this module exists to fix is that an omission (nothing
promoted to loop memory) currently looks identical to an absence (nothing to
promote). A nudge makes that difference visible instead of collapsing it. A
nudge never blocks a write or changes what gets stored.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from taskfw.dispatcher.phase import is_groomed, is_implemented, is_introspected

_DRIFT_NUDGE_INTERVAL = 8


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


def drift_reflection_nudge(
    active_task_id: str, active_task_title: str = "", active_task_phase: str = "",
    active_task_next_item: str = "", call_count: int | None = None,
    active_task_completed_items: list[str] | None = None,
) -> str | None:
    """Stateless awareness nudge for the active task, or None when there isn't one.

    Originally a throttled port of claude-hooks' _maybe_drift_reflection_nudge
    (task:f1d46386), firing every Nth call under an active task. Simplified to
    fire on every call instead (task:8be768df): the interval existed only to
    keep a nudge wired onto taskfw's own MCP tools from nagging on every one of
    them, but the trigger has since moved to a taskfw-owned PostToolUse hook
    (taskfw/drift_hook.py) that sees every tool call in the session — Bash,
    Read, Write, Edit included, not just taskfw's own. A counter tied to
    taskfw-tool-call volume was never the right throttle for that wider
    surface, and removing it also removes the only piece of cross-process
    state this module held, which the hook (a fresh subprocess per call) could
    never have shared with a live MCP server session anyway. No active task
    means nothing to remind about.

    Every-call firing on that wider surface turned out too noisy in practice,
    so task:1c8f0815 reintroduces the throttle — but sourced differently, since
    this function still can't hold cross-process state itself. claude-hooks'
    dispatcher.py runs as a long-running per-session process (unlike this
    hook's fresh-subprocess-per-call shape) and already tracks an analogous
    per-session counter for its own context-size nudge, so it now counts
    PostToolUse calls and passes the raw count in via drift_hook.py's stdin
    payload. The %N==0 decision stays here, in taskfw, keeping this module the
    sole owner of whether the nudge fires — claude-hooks supplies data, not
    policy. call_count of None (no counter supplied, e.g. a manual/non-
    claude-hooks caller) fires on every call, matching pre-task:1c8f0815
    behavior for callers that never opted into the counter.

    AN AWARENESS NUDGE, NOT A DETECTION MECHANISM. This function has no view
    into what happened during the call it's attached to — it cannot tell drift
    from a stretch of perfectly on-task work, because it isn't given anything
    to compare against. What it does is put the task label back in front of
    the caller on every qualifying call, so awareness has to be re-established
    continuously rather than fading silently after the one-time announcement
    at activation. Whether that re-established awareness catches anything is
    left entirely to whoever reads it; the mechanism's job ends at making the
    check-in happen, not at judging its outcome.
    """
    if not active_task_id:
        return None
    if call_count is not None and call_count % _DRIFT_NUDGE_INTERVAL != 0:
        return None
    label = f"task:{active_task_id}" + (f" ({active_task_title})" if active_task_title else "")
    if active_task_phase:
        label += f" [{active_task_phase}]"
    if active_task_completed_items:
        done_list = "; ".join(active_task_completed_items)
        label += f" — done: {done_list}"
    if active_task_next_item:
        label += f' — next: "{active_task_next_item}"'
    return (
        f"{label} is active. Notice — does this call still serve the task's stated "
        "intent, or has it drifted into something adjacent? Accuracy matters more "
        "than speed here: verify each step rather than batching changes and hoping "
        "they land."
    )


def finish_nudge(task) -> str | None:
    """Advisory nudge for tasks__finish, or None when there's nothing to say.

    Host-agnostic replacement for the reminder claude-hooks' external
    PostToolUse hook currently prints after a task closes — this fires from
    inside taskfw itself, so it holds regardless of host or whether that
    separate hook process happens to be running.
    """
    if is_introspected(task):
        return None
    return f"task:{task.id} closed with no introspection report yet. Consider running /task-introspection."


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


def stale_memory_nudge(memory: dict[str, Any]) -> str | None:
    """Advisory nudge for task_memory__link, or None when there's nothing to say.

    Stateless by design, matching finish_reminder_nudge's precedent: checks
    the memory's current standing on every call rather than only on the
    link that caused the transition. An idempotent re-link against an
    already-disputed memory re-fires this, same as finish_reminder_nudge
    re-fires on a later save that still finds a task 100%-done-but-open —
    task_memory__link is an infrequent call, not a hot path that needs
    drift_reflection_nudge-style throttling.

    Fires only on 'disputed' or 'contradicted' standing, never 'superseded':
    a superseded memory is already flagged by definition, and re-nudging
    about it would be noise about a decision already made.
    """
    if memory.get("standing") not in ("disputed", "contradicted"):
        return None
    return (f"memory {memory['slug']} is now {memory['standing']} — "
            f"consider task_memory__supersede or reviewing its evidence.")


def ungroomed_progress_nudge(task) -> str | None:
    """Advisory nudge for tasks__check_item/tasks__update, or None when there's nothing to say.

    Fires when checklist progress exists (at least one resolution item
    checked) but task.grooming is still empty — implementation is underway
    on a task that never went through /task-grooming, the step that exists
    specifically to remove uncertainty before building starts. Stateless by
    design, matching finish_reminder_nudge and stale_memory_nudge: checks
    the task's current state on every qualifying save rather than firing
    once and remembering it fired, since re-observing "still ungroomed" on a
    later save costs nothing extra.

    Deliberately silent once task.grooming is non-empty, even if the actual
    grooming pass happened after implementation started — checked via
    is_groomed, the same predicate task_phase()'s "groomed" is built from,
    so this nudge and task_phase can never disagree about what counts as
    groomed.
    """
    done, _total = task.progress
    if done == 0 or is_groomed(task):
        return None
    return (
        f"task:{task.id} has checklist progress but was never groomed. "
        "Consider whether uncertainty was actually removed before starting, "
        "or run /task-grooming now to capture it retroactively."
    )


def loop_debt_nudge(skipped: int, tasks_examined: int) -> str | None:
    """Advisory nudge for tasks__set_active, or None when there's no debt — task:07f9270c.

    Stateless and unthrottled, matching finish_reminder_nudge's precedent: the
    debt is recomputed from taskfw.accuracy.loop_debt on every call rather
    than tracked separately, so it can never disagree with what
    tasks__grooming_accuracy reports about the same tasks. Fires only when
    skipped > 0 — a clean loop produces silence, not a zero report, same as
    every other nudge here.
    """
    if not skipped:
        return None
    return (
        f"{skipped} of the last {tasks_examined} finished task(s) had predicted "
        "risks that were never graded. Consider running /task-introspection on them."
    )


def task_debt_nudge(task_id: str, ungraded: int) -> str | None:
    """Advisory nudge for tasks__set_active, or None when the task has no debt of its own.

    Companion to loop_debt_nudge: that one is about the loop across recent
    finished tasks, this one is about the specific task just made active,
    whatever its status — a task can carry ungraded risks from a prior
    grooming pass whether or not it has been finished yet.
    """
    if not ungraded:
        return None
    return (
        f"task:{task_id} has {ungraded} risk(s) from a prior grooming pass "
        "that were never graded. Grade them (task-introspection) before this task closes."
    )
