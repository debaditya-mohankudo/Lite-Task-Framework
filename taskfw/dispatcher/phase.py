"""Pure, fully-derived task-state helpers.

Shared by taskfw.dispatcher.nudges and mcp_server.py's tasks__phase tool, so
neither can disagree about where a task stands in the grooming ->
implementation -> introspection loop. Nothing here is stored: every value is
recomputed from the task's own fields on every call, so it can never drift
out of sync with the checklist or grooming/introspection history it reads.
"""
from __future__ import annotations


def is_groomed(task) -> bool:
    """Whether task has been through a grooming pass.

    A task is groomed exactly when its grooming findings are non-empty —
    there is no separate groomed_at flag, matching task-grooming/skill.md's
    own definition. The single predicate task_phase and every caller that
    needs this specific signal (e.g. ungroomed_progress_nudge) share, so a
    future change to what "groomed" means only has to change here.
    """
    return bool(task.grooming)


def is_introspected(task) -> bool:
    """Whether task has at least one introspection report.

    The single predicate task_phase and every caller that needs this
    specific signal (e.g. finish_nudge) share, so a future change to what
    "introspected" means only has to change here.
    """
    return bool(task.introspection)


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


def task_phase(task) -> dict[str, bool]:
    """Where a task stands in the grooming -> implementation -> introspection loop.

    Fully derived from fields the task already carries — never a stored
    status. Folding this into a new column or into active_task (a scope ->
    task_id pointer, not a per-task field) would duplicate a rule this
    module and lifecycle.py already own; see task:bf95ced8's grooming for
    why that was rejected. Composed from the three single-source predicates
    above rather than re-testing the underlying fields itself, so a change
    to what any one of them means changes this dict too, automatically.
    """
    return {
        "groomed": is_groomed(task),
        "implemented": is_implemented(task),
        "introspected": is_introspected(task),
    }
