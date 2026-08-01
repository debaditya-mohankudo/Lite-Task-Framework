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
"""
from __future__ import annotations

import sqlite3


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
