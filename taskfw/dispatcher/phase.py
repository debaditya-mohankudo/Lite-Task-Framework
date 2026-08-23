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


def next_open_item(task) -> str | None:
    """Text of the first unchecked resolution item, or None if there isn't one.

    Derived from task.resolution on every call, same reasoning as is_implemented
    and task_phase: a stored "current item" pointer could disagree with the
    checklist the moment an item gets checked off elsewhere, but a value
    recomputed from the list itself cannot.
    """
    for item in task.resolution:
        if not item.done:
            return item.text
    return None


def completed_items(task) -> list[str]:
    """Text of every checked resolution item, in checklist order.

    Same derivation as next_open_item: recomputed from task.resolution on
    every call rather than tracked separately, so it can never disagree with
    the checklist itself.
    """
    return [item.text for item in task.resolution if item.done]


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


def phase_label(phase: dict[str, bool]) -> str:
    """Collapse task_phase's three independent booleans into the single word a
    reader expects — "grooming", "implementation", "introspection", or "done".

    task_phase deliberately stays three booleans (a task can be re-groomed
    after being implemented, introspected without every checklist item done,
    etc.) so this makes an ordering call the booleans themselves don't: the
    first stage not yet reached, in grooming -> implementation ->
    introspection order. Only a caller that wants a single label for display
    (drift_reflection_nudge) needs this; tasks__phase keeps returning the raw
    booleans so it never disagrees with task_phase itself.
    """
    if not phase["groomed"]:
        return "grooming"
    if not phase["implemented"]:
        return "implementation"
    if not phase["introspected"]:
        return "introspection"
    return "done"
