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

The package is split three ways along the same seam the concept store now
tracks (task:<split-advisory-nudge-dispatcher>): dispatcher.chassis is pure
mechanism (log a call, run a hook, apply a nudge to a result — knows nothing
about what any nudge says), dispatcher.phase is pure derived task state
(shared by both the nudges below and mcp_server.py's tasks__phase, so neither
can disagree about where a task stands), and dispatcher.nudges is the actual
firing conditions, each one wired onto a specific MCP tool. Every name any of
the three submodules exposed under the old flat module is re-exported here
unchanged, so `from taskfw import dispatcher; dispatcher.tool_called(...)` and
`from taskfw.dispatcher import task_phase` keep working exactly as before —
this split is purely internal organisation, not a change in what's callable.
"""
from __future__ import annotations

from taskfw.dispatcher.chassis import apply_nudge, combine, tool_called
from taskfw.dispatcher.nudges import _DRIFT_NUDGE_INTERVAL  # noqa: F401 — re-exported for test_drift_hook
from taskfw.dispatcher.nudges import (
    drift_reflection_nudge,
    finish_nudge,
    finish_reminder_nudge,
    introspection_nudge,
    loop_debt_nudge,
    stale_memory_nudge,
    task_debt_nudge,
    ungroomed_progress_nudge,
)
from taskfw.dispatcher.phase import (
    completed_items,
    is_groomed,
    is_implemented,
    is_introspected,
    next_open_item,
    phase_label,
    task_phase,
)

__all__ = [
    "apply_nudge",
    "combine",
    "tool_called",
    "drift_reflection_nudge",
    "finish_nudge",
    "finish_reminder_nudge",
    "introspection_nudge",
    "loop_debt_nudge",
    "stale_memory_nudge",
    "task_debt_nudge",
    "ungroomed_progress_nudge",
    "completed_items",
    "is_groomed",
    "is_implemented",
    "is_introspected",
    "next_open_item",
    "phase_label",
    "task_phase",
]
