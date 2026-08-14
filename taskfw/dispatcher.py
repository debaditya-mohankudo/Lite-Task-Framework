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

from taskfw.log import get_logger

log = get_logger(__name__)


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

    AN AWARENESS NUDGE, NOT A DETECTION MECHANISM. This function has no view
    into what happened during the call it's attached to — it cannot tell drift
    from a stretch of perfectly on-task work, because it isn't given anything
    to compare against. What it does is put the task label back in front of
    the caller on every call, so awareness has to be re-established
    continuously rather than fading silently after the one-time announcement
    at activation. Whether that re-established awareness catches anything is
    left entirely to whoever reads it; the mechanism's job ends at making the
    check-in happen, not at judging its outcome.
    """
    if not active_task_id:
        return None
    label = f"task:{active_task_id}" + (f" ({active_task_title})" if active_task_title else "")
    if active_task_phase:
        label += f" [{active_task_phase}]"
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
    grooming pass happened after implementation started — the field being
    non-empty is the same signal task_phase()'s "groomed" already treats as
    authoritative (task-grooming/skill.md: "a task is groomed when its
    grooming is non-empty"), so this nudge and task_phase can never disagree
    about what counts as groomed.
    """
    done, _total = task.progress
    if done == 0 or task.grooming:
        return None
    return (
        f"task:{task.id} has checklist progress but was never groomed. "
        "Consider whether uncertainty was actually removed before starting, "
        "or run /task-grooming now to capture it retroactively."
    )


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


def apply_nudge(result: dict[str, Any], key: str, nudge: str | None) -> None:
    """Set `result[key] = nudge`, or leave `result` untouched when there's nothing to say.

    One shared wrapper for every nudge function's result — call the nudge
    function yourself first (its own signature is the real variation between
    introspection_nudge/drift_reflection_nudge/finish_nudge/
    finish_reminder_nudge; there is nothing left to template once the "if
    truthy, set the key" step is factored out).

    Logs the firing here, not in each nudge function, so every nudge is
    observable in the `logs` table by construction — a nudge type added
    later gets logged for free just by going through this function, instead
    of remembering to add a log line at each call site.
    """
    if nudge:
        result[key] = nudge
        log.info("nudge=%s fired: %s", key, nudge)


class tool_called:
    """Wraps every MCP tool call: unconditional logging, plus an optional nudge hook.

    Usage:

        with dispatcher.tool_called("tasks__x", post=lambda r: ...) as call:
            call.result = {"ok": True, ...}
            return call.result

    In this codebase there is exactly one tool_called per call, opened by
    the `_tool` decorator in mcp_server.py, which is the one route every
    MCP tool goes through — registration, logging, and hook, all in one
    place (task:58782207). Nothing here is specific to that decorator,
    though; tool_called is usable directly wherever a tool needs it.

    `name` is the tool being called — every exit logs exactly one line,
    `tool=<name> OK` / `tool=<name> REFUSE rule=<rule>` / `tool=<name> ERROR
    <type>: <msg>`. This is the only place in the framework that logs tool
    usage itself, rather than the state changes a tool happened to cause —
    those are store.py's and lifecycle.py's job and unaffected by this.

    `pre` runs on entry. `post` (the "hook") runs on a clean, non-refused
    exit — refused means `call.result` is a dict containing an "error" key.
    A non-dict result (e.g. tasks__list's bare list) is never a refusal, so
    it's treated as success for both logging and the hook; `post` is only
    actually invoked when `call.result` is a dict, since apply_nudge (what
    every hook calls) needs somewhere to write the nudge key. Mutating
    `call.result` in `post` is visible in what the function actually
    returns: `return call.result` evaluates the reference before `__exit__`
    runs as part of unwinding the `with` block, and `post` mutates that same
    dict in place.

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
        name: str,
        pre: Callable[[], None] | None = None,
        post: Callable[[dict[str, Any]], None] | None = None,
    ):
        self.name = name
        self.pre = pre
        self.post = post
        self.result: Any = {}

    def __enter__(self) -> "tool_called":
        if self.pre:
            self.pre()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is not None:
            log.info("tool=%s ERROR %s: %s", self.name, exc_type.__name__, exc)
            return
        refused = isinstance(self.result, dict) and "error" in self.result
        if refused:
            log.info("tool=%s REFUSE rule=%s", self.name, self.result.get("rule"))
            return
        log.info("tool=%s OK", self.name)
        if self.post and isinstance(self.result, dict):
            self.post(self.result)


def combine(*hooks: Callable[[dict[str, Any]], None]) -> Callable[[dict[str, Any]], None]:
    """A composite hook: runs each of `hooks` in order, in the same
    Callable[[dict], None] shape as any single one.

    Lets a tool that needs several nudges (e.g. tasks__update wants both
    drift_reflection_nudge and finish_reminder_nudge) compose them at its
    own call site instead of tool_called or mcp_server.py's `_tool` needing
    to know the difference between one hook and several (task:58782207).
    Each hook mutates the same `result` dict in place via apply_nudge, so
    order only matters if two hooks ever wrote the same key — none do today.
    """
    def composite(result: dict[str, Any]) -> None:
        for hook in hooks:
            hook(result)
    return composite
