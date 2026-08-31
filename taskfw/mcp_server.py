"""MCP server — the portable interface to the framework.

Any MCP host reaches the whole framework through these tools; MCP is the
portability layer, so there is no neutral abstraction on top of it.

Tools are thin. Each one validates through taskfw.lifecycle and then calls
taskfw.store, so there is exactly one implementation of every rule, shared with
the optional hooks. A tool cannot enforce a different set than a hook because
neither owns the rules.
"""
from __future__ import annotations

import functools
import json
import os
from datetime import datetime, timezone
import urllib.error
import urllib.request
from typing import Any, Callable

from mcp.server import MCPServer

from taskfw import dispatcher, lifecycle
from taskfw.accuracy import _task_grading, grooming_accuracy, loop_debt
from taskfw.concepts import ConceptStore
from taskfw.config import DEFAULT_RECALL_LIMIT
from taskfw.context import TaskContext
from taskfw.db.connect import connect
from taskfw.log import get_logger
from taskfw.memory import MemoryStore, Rejected
from taskfw.risk import coerce, normalise_text
from taskfw.scope import derive as derive_scope
from taskfw.task import ResolutionItem, Task, new_id
from taskfw.store import TaskStore

log = get_logger(__name__)

#: mcp >= 1.26 renamed FastMCP to MCPServer. The decorator API is unchanged and
#: leaves the wrapped function directly callable, which is what lets the tests
#: exercise tools without standing up a transport.
mcp = MCPServer("taskfw")

_store: TaskStore | None = None


def store() -> TaskStore:
    """Lazily open the store, so importing this module never touches disk."""
    global _store
    if _store is None:
        _store = TaskStore()
    return _store


def set_store(s: TaskStore) -> None:
    """Point the tools at a specific store — used by tests."""
    global _store, _memory
    _store = s
    # Memories live in the same database as tasks, so a test store swap must
    # move both or the two halves point at different files.
    _memory = MemoryStore(conn=s.conn) if s is not None else None


_memory: MemoryStore | None = None


def memory() -> MemoryStore:
    """Lazily open the memory store. Shares the task database by design.

    ONE FILE, because a second one would be a second source to check and keep
    track of without buying anything. The citation from a memory to its task is
    validated here at the tool layer, not by a foreign key, so splitting the
    files would cost nothing — and gain nothing either. A fresh environment
    starts with an empty database whichever way it is arranged.
    """
    global _memory
    if _memory is None:
        _memory = MemoryStore(conn=store().conn)
    return _memory


_log_conn = None


def log_conn():
    """Always config.db_path() — deliberately not store().conn.

    Mirrors taskfw.log.SQLiteHandler's own choice, for the same reason: the
    handler that writes the `logs` table is a process-wide singleton with no
    store to draw a path from, so the tool that reads it back stays on the
    same fixed target rather than following whatever set_store() swapped in.
    The one tool in this module that doesn't honor a test's store swap.
    """
    global _log_conn
    if _log_conn is None:
        _log_conn = connect()
    return _log_conn


def _scope() -> str:
    """Active-task scope. Per-workspace when there is one, else global.

    NOT the same thing as `Task.scope`, and deliberately still a raw path.
    This keys an in-memory pointer at "which task am I working on right now",
    which is ephemeral and per-*directory*: two worktrees of one repo are two
    places someone can be working, and each deserves its own active task.
    `derive_scope()` would collapse them onto one, which is the right answer
    for "which project owns this task" and the wrong one for "which task is
    open in this window". Two different questions, so two different functions.
    """
    return os.environ.get("TASKFW_SCOPE") or os.getcwd()


def _denied(d: lifecycle.Decision) -> dict:
    return {"error": d.reason, "rule": d.rule}


def _tool(hook: Callable[[dict[str, Any]], None] | None = None):
    """The one route every MCP tool goes through: registration, unconditional
    logging, and an optional post-success hook, all in one place (task:58782207).

    Replaces both `@mcp.tool()` + a separate logging decorator stacked on top,
    and the two former helpers, `_drift_reflection_call`/`_drift_reflection_read`,
    that existed only to wire a hook onto some tools but not others (task:58782207
    grooming — a pushback from "why is this a decorator at all" through "one
    route, no registry" landed here). A tool decorated with `_tool()` alone
    gets logging with no hook; `_tool(hook=...)` adds one. There is no other
    way to register a tool with this module — skip `_tool` and the function
    is not registered at all, not registered-but-unlogged, so a new tool
    cannot silently end up outside this mechanism.

    `hook` is a plain `Callable[[dict], None]`, the same shape tool_called's
    `post` always took — see dispatcher.combine for composing more than one
    onto a single tool without `_tool` or tool_called needing to know the
    difference between one hook and several.
    """
    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            with dispatcher.tool_called(fn.__name__, post=hook) as call:
                call.result = fn(*args, **kwargs)
                return call.result
        mcp.tool()(wrapper)
        return wrapper
    return decorator


# There is no active-task / drift-reflection nudge anywhere in taskfw
# (task:00d9483f). An MCP-tool-wrapper trigger (_drift_reflection_hook, wired
# onto 7 of this module's tools) was tried and removed (task:8be768df) because
# it only saw taskfw's own calls; a PostToolUse hook (taskfw/drift_hook.py,
# task:1c8f0815) that saw every tool call was tried and removed too — it dragged
# in a cross-repo, cross-process call-count contract to re-print a label taskfw
# already announces once at tasks__set_active. The active task is announced
# there and not re-surfaced, matching "context is pulled, never pushed". Do not
# re-add a periodic active-task reminder in any form.


def _refetch(result: dict[str, Any]) -> Task | None:
    """The task a hook needs, re-fetched by the id already in `result`.

    Every hook below needs this same lookup — factored out once rather than
    repeated per hook. Re-fetching instead of closing over the tool's own
    mutated local is what lets a hook be a plain Callable[[dict], None],
    callable from outside the tool it applies to: the mutation is already
    persisted (store().save() already ran) by the time any hook fires, so a
    fresh read is exactly as correct (task:58782207).
    """
    return store().get(result["id"])


def _finish_reminder_hook(result: dict[str, Any]) -> None:
    """finish_reminder_nudge, for tasks__update/tasks__check_item."""
    task = _refetch(result)
    if task:
        dispatcher.apply_nudge(result, "finish_reminder_nudge", dispatcher.finish_reminder_nudge(task))


def _ungroomed_progress_hook(result: dict[str, Any]) -> None:
    """ungroomed_progress_nudge, for tasks__update/tasks__check_item."""
    task = _refetch(result)
    if task:
        dispatcher.apply_nudge(result, "ungroomed_progress_nudge", dispatcher.ungroomed_progress_nudge(task))


def _finish_hook(result: dict[str, Any]) -> None:
    """finish_nudge, for tasks__finish. task.introspection is untouched by
    tasks__finish's own mutation (only status changes), so the re-fetched
    task is identical to the one the tool itself had in hand."""
    task = _refetch(result)
    if task:
        dispatcher.apply_nudge(result, "introspection_nudge", dispatcher.finish_nudge(task))


def _stale_memory_hook(result: dict[str, Any]) -> None:
    """stale_memory_nudge, for task_memory__link.

    The memory is already in `result["memory"]` (task_memory__link's own
    return shape re-fetches it after linking), so no separate lookup is
    needed the way the task hooks above need _refetch.
    """
    memory = result.get("memory")
    if memory:
        dispatcher.apply_nudge(result, "stale_memory_nudge", dispatcher.stale_memory_nudge(memory))


#: Recent-finished-tasks window loop_debt walks on activation — smaller than
#: tasks__grooming_accuracy's default 25 because the activation paths it rides
#: (tasks__set_active, tasks__create) are hit far more often per session
#: (task:07f9270c's grooming).
_LOOP_DEBT_LIMIT = 10


def _loop_debt_hook(result: dict[str, Any]) -> None:
    """loop_debt_nudge / task_debt_nudge, for tasks__set_active and
    tasks__create — task:07f9270c, create added task:356b3ada.

    Two independent nudges under two keys: one about recent finished tasks
    across the store, one about the specific task just made active. Both
    derive from taskfw.accuracy's _task_grading/loop_debt, the same
    classification tasks__grooming_accuracy uses, so none of the three can
    disagree about what counts as ungraded.

    The just-activated task's id is under a different key per tool —
    `active` from tasks__set_active, `id` from tasks__create — so both are
    tried. This is what keeps the debt reminder alive across the
    finish-A → create-B flow that task:1105f979 made the default by having
    create activate and telling callers to drop the trailing set_active.
    For a freshly created task task_debt_nudge is always silent (no risks
    yet); only loop_debt_nudge has anything to say there.
    """
    task = store().get(result.get("active") or result.get("id") or "")
    if task is None:
        return
    # Scoped to the project being worked in. This nudge fires on every
    # activation, so an unscoped count meant a debt figure driven by
    # another repo's tasks could interrupt work here with no way to tell —
    # the accepted risk recorded on concept:grooming-accuracy-aggregate.
    debt = loop_debt(store(), limit=_LOOP_DEBT_LIMIT, scope=derive_scope())
    dispatcher.apply_nudge(
        result, "loop_debt_nudge",
        dispatcher.loop_debt_nudge(debt["skipped_introspection"], debt["tasks_examined"]),
    )
    _, task_ungraded, _ = _task_grading(task)
    dispatcher.apply_nudge(result, "task_debt_nudge", dispatcher.task_debt_nudge(task.id, task_ungraded))


def _introspection_hook(result: dict[str, Any]) -> None:
    """introspection_nudge, for tasks__add_introspection.

    introspection_nudge needs the report itself, which is one of the tool's
    arguments rather than anything in `result` — re-fetched as
    task.introspection[-1], the entry the tool's own body just appended and
    saved, rather than threading the argument through result just for this.
    """
    task = _refetch(result)
    if task and dispatcher.is_introspected(task):
        dispatcher.apply_nudge(
            result, "memory_nudge",
            dispatcher.introspection_nudge(task.introspection[-1], result["id"], store().conn),
        )


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

@_tool()
def tasks__context(task_id: str = "", verbosity: str = "full") -> dict[str, Any]:
    """The whole bundle for a task: object, decisions, grooming, graph, commits, related, lessons.

    This is the main entry point — with no prompt injection, it is how an agent
    picks up a task's context. Omit task_id to use the active task.
    verbosity: "full" to start work, "summary" for identity and open items only.

    `related` and `lessons` are the two approximate sections and the first two
    trimmed; everything else is an exact lookup. `lessons` are loop memories
    matching this task, each carrying its derived standing — treat one marked
    `disputed` or `contradicted` as a claim to check, not as settled fact.
    """
    task_id = task_id or store().get_active(_scope()) or ""
    if not task_id:
        return {"error": "No task_id given and no active task set."}
    # Pass the shared memory store rather than letting TaskContext open its
    # own: MemoryStore.__init__ commits a CREATE VIRTUAL TABLE IF NOT EXISTS,
    # which has no business running on every context read.
    return TaskContext(store(), memory=memory()).bundle(task_id, verbosity)


@_tool()
def tasks__grooming_accuracy(limit: int = 25, scope: str = "") -> dict[str, Any]:
    """How well grooming has been predicting, across recent finished tasks.

    The only tool that reads across tasks rather than into one. Grades live
    inside each task's grooming, so without this a pattern is writable and
    unreadable — and "repeated `wrong` means grooming asks the wrong questions"
    stays advice nobody can act on.

    Tallies are recomputed from the per-risk grades, never read from an
    introspection report's self-reported count.
    """
    # Global by default: the aggregate is a deliberate, occasional read, and
    # a cross-project pattern in it is a real finding rather than noise. Pass
    # scope="." (or any path) to narrow to one project. Either way the result
    # names the scope it counted, so the answer is never ambiguous.
    return grooming_accuracy(store(), limit=limit,
                             scope=derive_scope(scope) if scope else None)


@_tool()
def tasks__logs(logger: str = "", level: str = "", limit: int = 50) -> dict[str, Any]:
    """Operational log lines from the `logs` table, most recent first.

    The second observability dimension alongside task_events: this is what
    the code was doing internally (every save, DENY, nudge), not what was
    decided or shipped. Reads config.db_path() directly rather than the
    active store — see log_conn()'s own docstring for why.

    logger/level filter by exact match when given; both empty returns
    everything, most recent limit rows.
    """
    sql = "SELECT logger, level, message, ts FROM logs"
    where, params = [], []
    if logger:
        where.append("logger=?")
        params.append(logger)
    if level:
        where.append("level=?")
        params.append(level)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    rows = log_conn().execute(sql, params).fetchall()
    return {"logs": [dict(r) for r in rows]}


@_tool()
def tasks__log_skill_invocation(skill: str, task_id: str = "") -> dict[str, Any]:
    """Record that a skill was invoked, for skill-usage observability.

    Thin wrapper around the existing get_logger()/SQLiteHandler machinery —
    no new sink, no new table. Scoped narrowly to invocation tracking rather
    than a generic log-write tool, so it can't become a second home for
    decisions or notes, which already have one. get_logger() prefixes every
    name with "taskfw.", so query back with
    tasks__logs(logger=f"taskfw.skill.{skill}").

    task_id is not validated — this is an observability signal, not a
    referentially-integral record, matching `logs` having no foreign key to
    tasks at all.
    """
    get_logger(f"skill.{skill}").info(f"invoked task={task_id or '-'}")
    return {"ok": True, "skill": skill, "task_id": task_id}


@_tool()
def tasks__get(task_id: str) -> dict[str, Any]:
    """Return one task object."""
    task = store().get(task_id)
    return task.to_dict() if task else {"error": f"No task {task_id!r}"}


@_tool()
def tasks__phase(task_id: str) -> dict[str, Any]:
    """Where a task stands in the grooming -> implementation -> introspection loop.

    Fully derived from the task's existing grooming/resolution/introspection
    fields (see dispatcher.task_phase) — never a stored status, so this can
    never disagree with the fields it reads. Saves eyeballing three fields on
    tasks__get's payload by hand.
    """
    task = store().get(task_id)
    if not task:
        return {"error": f"No task {task_id!r}"}
    return {"id": task.id, **dispatcher.task_phase(task)}


@_tool()
def tasks__list(status: str = "open,blocked", epic: bool | None = None, parent: str = "", limit: int = 50) -> list[dict]:
    """List tasks. status is comma-separated; empty means every status.

    epic omitted lists both; epic=true lists only epics, epic=false only
    non-epic tasks. Rows come back ordered by updated_at, most recently
    touched first.
    """
    statuses = tuple(s.strip() for s in status.split(",") if s.strip()) or None
    tasks = store().list(status=statuses, epic=epic, parent=parent or None, limit=limit)
    return [
        {"id": t.id, "epic": t.epic, "status": t.status, "title": t.title,
         "parent": t.parent, "progress": list(t.progress),
         "created_at": t.created_at, "updated_at": t.updated_at}
        for t in tasks
    ]


@_tool()
def tasks__search(query: str, limit: int = 25) -> list[dict]:
    """Full-text search over titles, motivation, notes, tags, files, and checklist items."""
    return [
        {"id": t.id, "epic": t.epic, "status": t.status, "title": t.title}
        for t in store().search(query, limit=limit)
    ]


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

@_tool(hook=_loop_debt_hook)
def tasks__create(
    title: str,
    epic: bool = False,
    parent: str = "",
    motivation: str = "",
    resolution: list[str] | None = None,
    files: list[str] | None = None,
    tags: list[str] | None = None,
    notes: str = "",
) -> dict[str, Any]:
    """Create a task or epic.

    epic=True groups other tasks and cannot have a parent; epic=False (the
    default) does work. resolution is a list of checklist item texts — there
    is no body template to satisfy and no required sections, because the
    object's shape is the schema.

    The response's related_candidates is advisory only — titles that share
    words with an existing task, surfaced for a human to review. Nothing here
    ever creates a tasks__link edge; that stays a deliberate, separate call.

    Sets the new task as the active one for this scope (task:1105f979),
    replacing whatever was active before it. The active pointer is a single
    ephemeral in-memory value (task:f5ace343), and creating a task is the
    start of a pass through the loop — so activation belongs here rather than
    being a separate step every caller has to remember. Nothing auto-clears
    the pointer again: it lives until tasks__clear_active is called
    explicitly, which the task-introspection skill does at its final step.

    Because create is an activation path, it carries _loop_debt_hook too
    (task:356b3ada) — finishing a task with ungraded risks and immediately
    creating the next one still surfaces loop_debt_nudge, which would
    otherwise be lost now that the trailing tasks__set_active is discouraged.
    """
    task = Task(
        title=title, epic=epic, parent=parent or None, motivation=motivation,
        resolution=[ResolutionItem(t) for t in (resolution or [])],
        files=files or [], tags=tags or [], notes=notes,
        # Recorded at creation, when the workspace is a fact in hand, and
        # never derived again afterwards — a task does not change project
        # because someone later read it from somewhere else.
        scope=derive_scope(),
    )
    parent_task = store().get(task.parent) if task.parent else None
    if task.parent and parent_task is None:
        return {"error": f"Parent {task.parent!r} does not exist."}
    decision = lifecycle.check_save(task, parent=parent_task)
    if not decision:
        return _denied(decision)
    store().save(task)
    scope = _scope()
    _set_and_broadcast(task.id, task.title, scope)
    result: dict[str, Any] = {"ok": True, "id": task.id, "epic": task.epic, "status": task.status}
    candidates = TaskContext(store()).related(task)
    if candidates:
        result["related_candidates"] = candidates
        # Logged, never linked: the audit trail is "what was surfaced at
        # create time", so a later reviewer can see which candidates a task
        # was shown against without a tasks__link edge ever being written.
        log.info(
            "tasks__create task=%s related_candidates=%s",
            task.id, ",".join(c["id"] for c in candidates),
        )
    return result


def _merge_grooming_risks(current_raw: list | None, incoming_raw: list | None) -> list[dict]:
    """Union current and incoming grooming risks by id — task:f24be6e4.

    Every other grooming field stays wholesale-replace; only `risks` merges,
    because a risk's `graded` value is the loop's primary evidence and a
    caller that omits a risk from a re-groom should never silently destroy
    its grade. Rules:

    - An incoming risk carrying an `id` that matches a current risk replaces
      that entry — this is how a risk gets reworded or regraded without
      losing its history, since the id (not the text) is its identity. If the
      incoming entry omits `graded` (or sends it empty) while the current
      entry was graded, the existing grade is kept: an ordinary reword must
      not silently reset introspection evidence just because the caller only
      meant to edit the text.
    - An incoming risk with no `id` is a brand-new prediction: this function
      assigns it one. A caller can never supply the id for a new risk — it is
      framework-assigned by construction, so no caller can collide with an
      existing one.
    - A current risk whose id is absent from the incoming list is dropped if
      ungraded (an ordinary retraction) but carried forward automatically if
      graded, so a grade can never vanish just because a re-groom's payload
      didn't repeat it.
    - A current risk written before this change has no id at all. It is
      matched to an incoming id-less risk by normalised text first, so its
      first post-migration re-groom picks up an id instead of duplicating —
      falling back to the same carry-forward-if-graded rule when nothing
      matches. Stored grooming from before this change is never rewritten.
    """
    current = [coerce(r) for r in (current_raw or [])]
    incoming = [coerce(r) for r in (incoming_raw or [])]

    current_by_id = {r["id"]: r for r in current if r.get("id")}
    current_idless = [r for r in current if not r.get("id")]
    consumed_ids: set[str] = set()
    consumed_idless: set[int] = set()

    merged: list[dict] = []
    for risk in incoming:
        rid = risk.get("id")
        if rid:
            current_entry = current_by_id.get(rid)
            if current_entry and current_entry.get("graded") and not risk.get("graded"):
                risk = dict(risk)
                risk["graded"] = current_entry["graded"]
            merged.append(risk)
            consumed_ids.add(rid)
            continue
        key = normalise_text(risk.get("text", ""))
        match_i = next(
            (i for i, cand in enumerate(current_idless)
             if i not in consumed_idless and key and normalise_text(cand.get("text", "")) == key),
            None,
        )
        new_entry = dict(risk)
        new_entry["id"] = new_id()
        if match_i is not None:
            consumed_idless.add(match_i)
        merged.append(new_entry)

    # Carry forward graded risks the incoming payload dropped by omission.
    for rid, risk in current_by_id.items():
        if rid not in consumed_ids and risk.get("graded"):
            merged.append(risk)
    for i, risk in enumerate(current_idless):
        if i not in consumed_idless and risk.get("graded"):
            new_entry = dict(risk)
            new_entry["id"] = new_id()
            merged.append(new_entry)

    return merged


@_tool(hook=dispatcher.combine(_finish_reminder_hook, _ungroomed_progress_hook))
def tasks__update(
    task_id: str,
    title: str = "",
    status: str = "",
    motivation: str = "",
    notes: str = "",
    resolution: list[str] | None = None,
    files: list[str] | None = None,
    tags: list[str] | None = None,
    grooming: dict | None = None,
) -> dict[str, Any]:
    """Update a task. Only the fields you pass are changed.

    Every field is replace-not-append, and that is explicit per field rather
    than ambiguous across one blob. A single free-text `body` argument makes
    replace-versus-append something the caller has to infer from prose, and
    inferring it wrong destroys content silently; naming each field makes the
    semantics visible at the call site instead.
    """
    current = store().get(task_id)
    if current is None:
        return {"error": f"No task {task_id!r}"}

    updated = Task.from_dict(current.to_dict())
    if title:
        updated.title = title
    if status:
        updated.status = status
    if motivation:
        updated.motivation = motivation
    if notes:
        updated.notes = notes
    if resolution is not None:
        updated.resolution = [ResolutionItem(t) for t in resolution]
    if files is not None:
        updated.files = files
    if tags is not None:
        updated.tags = tags
    if grooming is not None:
        merged_grooming = dict(grooming)
        merged_grooming["risks"] = _merge_grooming_risks(
            (current.grooming or {}).get("risks"), grooming.get("risks")
        )
        updated.grooming = merged_grooming

    parent_task = store().get(updated.parent) if updated.parent else None
    decision = lifecycle.check_save(updated, previous=current, parent=parent_task)
    if not decision:
        return _denied(decision)
    store().save(updated)
    return {"ok": True, "id": updated.id, "status": updated.status}


def _set_and_broadcast(task_id: str, task_title: str, scope: str) -> None:
    """Set the active task for scope and tell claude-hooks about it.

    Shared by tasks__create and tasks__set_active — both need "set, then tell
    claude-hooks what's active" and nothing more. Ticking a checklist item
    used to be a third caller (task:c88b8730 removed it): tasks__create now
    activates the new task (task:1105f979) and the task-implementation skill
    calls tasks__set_active when work begins, so ticking no longer needs to
    stand in for a forgotten activation.
    """
    store().set_active(task_id, scope)
    _push_active_task(scope, task_id, task_title)


def _clear_and_broadcast(scope: str) -> None:
    """Clear the active task for scope and tell claude-hooks it's gone.

    tasks__clear_active is now the only caller — nothing auto-clears the
    pointer on create or finish anymore (task:1105f979). Kept as a helper so
    "clear, then tell claude-hooks there is no active task" stays one thing.
    """
    store().clear_active(scope)
    _push_active_task(scope, "")


def _finish_task(task_id: str, reason: str = "") -> dict[str, Any]:
    """Mark a task done.

    Shared by tasks__finish and the auto-finish-on-last-item path in
    tasks__check_item (task:f302eb2b) so there is exactly one implementation
    of "what finishing a task does."

    Idempotent: finishing an already-done task succeeds rather than erroring,
    which follows from the same-status rule and makes retries safe. Finishing
    an abandoned task IS refused — abandoned is terminal and is not the state
    the caller asked for.

    Does not touch the active pointer (task:1105f979). A finished task stays
    active through introspection; tasks__clear_active — which task-introspection
    calls at its final step — is the only thing that deactivates it.
    """
    task = store().get(task_id)
    if task is None:
        return {"error": f"No task {task_id!r}"}
    decision = lifecycle.check_transition(task.status, "done")
    if not decision:
        return _denied(decision)
    if reason:
        kind_check = lifecycle.check_event_kind("status")
        if not kind_check:
            return _denied(kind_check)
    task.status = "done"
    store().save(task)
    if reason:
        store().add_event(task_id, reason, kind="status")
    return {"ok": True, "id": task_id, "status": "done"}


def _resolution_index_error(task: Task, index: int) -> str | None:
    """The error for an out-of-range checklist index, or None if it is in range.

    One home for the range rule, because two tools now ask it: tasks__check_item
    on its way to ticking, and tasks__add_decision, which has to ask BEFORE it
    writes its event so a rejected index cannot leave a decision behind with no
    tick to go with it.
    """
    if 0 <= index < len(task.resolution):
        return None
    return f"No item {index} — task has {len(task.resolution)}."


def _seconds_since_last_decision(task_id: str) -> float | None:
    """How long since the most recent decision event on this task, or None.

    None means the task carries no decision at all. That is an absence, and it
    is reported by leaving the figure out of the log line entirely rather than
    printing a zero, which would read as "ticked the instant it was answered" —
    the exact opposite of what it means.

    Measured, never acted on. Nothing branches on this number. It exists so the
    gap between "an item was answered in a decision" and "the item was ticked"
    is readable after the fact in tasks__logs, instead of being something a
    nudge has to guess at in the moment: which item a decision resolves is not
    a fact the framework has, so a nudge asking about it would be firing on
    judgement it does not hold (task:1f400ecf).

    An unparseable ts also yields None, which does conflate it with "no
    decision" in the log line — accepted, because the warning logged beside it
    is where that case is distinguishable, and because the schema writes
    datetime('now') and all 354 decision rows in the live store match that
    shape. The except is a guard against a bad row crashing a tick, not a case
    anyone has seen.
    """
    ts = store().last_event_ts(task_id, "decision")
    if not ts:
        return None
    try:
        recorded = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        log.warning("unparseable decision ts task=%s ts=%r", task_id, ts)
        return None
    return (datetime.now(timezone.utc) - recorded).total_seconds()


def _waited_field(task_id: str) -> str:
    """The ` waited=...` fragment for a check_item log line, or "" when there is
    no decision on this task to have waited since."""
    waited = _seconds_since_last_decision(task_id)
    return "" if waited is None else f" waited={waited:.0f}s"


def _apply_check_item(task_id: str, index: int, done: bool) -> dict[str, Any]:
    """Tick or untick one checklist item — the single implementation.

    Shared by tasks__check_item and tasks__add_decision's resolve path
    (task:1f400ecf), so the two cannot enforce different rules about what
    ticking an item does. Both auto-finish on the last open item because
    there is one path here, not two that happen to agree today. Ticking does
    NOT touch the active pointer (task:c88b8730).
    """
    task = store().get(task_id)
    if task is None:
        return {"error": f"No task {task_id!r}"}
    index_error = _resolution_index_error(task, index)
    if index_error:
        return {"error": index_error}
    task.resolution[index].done = done
    store().save(task)
    log.info(
        "check_item task=%s index=%s done=%s%s text=%r",
        task_id, index, done, _waited_field(task_id), task.resolution[index].text,
    )
    d, total = task.progress
    result: dict[str, Any] = {"ok": True, "id": task_id, "progress": {"done": d, "total": total}}
    if done and total > 0 and d == total:
        finish_result = _finish_task(task_id, reason="last checklist item checked off")
        if "error" in finish_result:
            result["finish_notice"] = finish_result["error"]
        else:
            result["status"] = finish_result["status"]
    return result


@_tool(hook=dispatcher.combine(_finish_reminder_hook, _ungroomed_progress_hook))
def tasks__check_item(task_id: str, index: int, done: bool = True) -> dict[str, Any]:
    """Tick or untick one resolution checklist item by its zero-based index.

    Ticking the last open item finishes the task the same way tasks__finish
    would (task:f302eb2b) — see _finish_task. Unticking never triggers that.
    Ticking does not touch the active pointer (task:c88b8730): tasks__create
    and the task-implementation skill are the activation paths now.

    Logs the item's text alongside its index: the generic `tool=tasks__check_item
    OK` line from tool_called (dispatcher.py) says a call happened but not which
    item, so intermediate items (not the last one, which gets its own `add_event`
    via _finish_task) would otherwise be indistinguishable from each other in
    tasks__logs.

    The same line carries `waited=<n>s` — how long since the task's most recent
    decision event — when the task has one. It is an observable and nothing
    reads it back: see _seconds_since_last_decision for why the gap is measured
    rather than gated on.
    """
    return _apply_check_item(task_id, index, done)


@_tool(hook=_finish_hook)
def tasks__finish(task_id: str, reason: str = "") -> dict[str, Any]:
    """Mark a task done.

    See taskfw.dispatcher: when the task closes with no introspection report
    yet, the response carries a non-blocking `introspection_nudge` — the
    host-agnostic equivalent of a hook reminding you to run
    /task-introspection while the context is fresh.
    """
    return _finish_task(task_id, reason)


@_tool(hook=_introspection_hook)
def tasks__add_introspection(task_id: str, report: dict) -> dict[str, Any]:
    """Append an introspection report to a task's history.

    Appends rather than replaces, unlike grooming: grooming records the current
    best understanding and only the latest pass is useful, whereas each
    introspection is evidence about a distinct execution and reads as a series.

    A report with lessons and a report with none otherwise leave the same
    trace in loop memory — nothing. See taskfw.dispatcher: when this report
    carries a lesson and the task has never cited a memory, the response
    carries a non-blocking `memory_nudge` so the omission is visible instead
    of silent.
    """
    task = store().get(task_id)
    if task is None:
        return {"error": f"No task {task_id!r}"}
    task.introspection.append(report)
    store().save(task)
    return {"ok": True, "id": task_id, "reports": len(task.introspection)}


def _resolved_item_hook(result: dict[str, Any]) -> None:
    """finish_reminder_nudge and ungroomed_progress_nudge for tasks__add_decision
    — but only on a call that actually ticked an item.

    A decision on its own changes no checklist state, so nudging about
    checklist state would be noise on the overwhelming majority of calls, and
    would change what a caller sees from a tool they reached for an unrelated
    reason. `progress` appears in the result only on the resolve path, which
    is exactly the condition, so this reads the result rather than the args.
    """
    if "progress" not in result:
        return
    task = _refetch(result)
    if task is None:
        return
    dispatcher.apply_nudge(result, "finish_reminder_nudge", dispatcher.finish_reminder_nudge(task))
    dispatcher.apply_nudge(result, "ungroomed_progress_nudge", dispatcher.ungroomed_progress_nudge(task))


@_tool(hook=_resolved_item_hook)
def tasks__add_decision(task_id: str, decision: str, resolves: int | None = None) -> dict[str, Any]:
    """Record a design decision. Surfaces in tasks__context, where it explains the task's shape.

    `resolves` is the zero-based index of the checklist item this decision
    ANSWERS. Pass it and the item is ticked in the same call, through the same
    path tasks__check_item uses (task:1f400ecf). It exists because an item
    whose completion criterion IS a recorded judgement ("consider whether X")
    is completed by the decision, while recording the decision on its own
    ticks nothing — task:d98a8f46 sat open at 5/6 with its last item already
    answered, one call from done and reading as work in progress.

    Which item a decision answers is the caller's knowledge and not a fact the
    framework holds, which is why this is a parameter rather than something a
    nudge asks about after the fact.

    The index is validated before anything is written, so a rejected index
    leaves no trace at all — a decision recorded with no tick beside it is the
    precise state this parameter exists to make unreachable, and a half-applied
    call would recreate it.

    Omitting `resolves` leaves the call exactly as it was.
    """
    task = store().get(task_id)
    if task is None:
        return {"error": f"No task {task_id!r}"}
    if resolves is not None:
        index_error = _resolution_index_error(task, resolves)
        if index_error:
            return {"error": index_error}
    kind_check = lifecycle.check_event_kind("decision")
    if not kind_check:
        return _denied(kind_check)
    store().add_event(task_id, decision, kind="decision")
    if resolves is None:
        return {"ok": True, "id": task_id}
    result = _apply_check_item(task_id, resolves, True)
    result["decision_recorded"] = True
    return result


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------

@_tool()
def tasks__link(from_id: str, to_id: str, rel: str = "relates_to") -> dict[str, Any]:
    """Create an edge between two tasks. Idempotent.

    rel must be one of TASK_EDGE_RELATIONS — see lifecycle.check_link_rel.
    """
    for tid in (from_id, to_id):
        if store().get(tid) is None:
            return {"error": f"No task {tid!r}"}
    decision = lifecycle.check_link_rel(rel)
    if not decision:
        return _denied(decision)
    return {"ok": True, "created": store().link(from_id, to_id, rel)}


@_tool()
def tasks__unlink(from_id: str, to_id: str, rel: str = "") -> dict[str, Any]:
    """Remove an edge, or every edge between two tasks when rel is omitted."""
    return {"ok": True, "removed": store().unlink(from_id, to_id, rel or None)}


@_tool()
def tasks__edges(task_id: str) -> dict[str, Any]:
    """Edges touching a task, both directions."""
    return store().edges(task_id)


@_tool()
def tasks__format_commit_message(task_id: str, subject: str, body: str = "") -> dict[str, Any]:
    """Format a commit message in the canonical shape: subject, blank, task:<id>, blank, body.

    Formatting only — no filesystem or git side effects. The caller still
    writes the returned message to a file and runs `git commit -F <path>`
    themselves; this exists so that shape is produced once, correctly,
    instead of re-assembled by hand from the /commit skill's prose every time.
    """
    if store().get(task_id) is None:
        return {"error": f"No task {task_id!r}"}
    subject = subject.strip()
    if not subject:
        return {"error": "subject must not be empty"}
    if "\n" in subject:
        return {"error": "subject must be a single line"}
    if subject.endswith("."):
        subject = subject[:-1]
    message = f"{subject}\n\ntask:{task_id}"
    body = body.strip()
    if body:
        message += f"\n\n{body}"
    return {"ok": True, "message": message}


@_tool()
def tasks__add_commit(task_id: str, sha: str, repo: str = "") -> dict[str, Any]:
    """Record that a commit implemented a task. Idempotent."""
    if store().get(task_id) is None:
        return {"error": f"No task {task_id!r}"}
    recorded = store().add_commit(task_id, sha, repo)
    return {"ok": True, "recorded": recorded}


# ---------------------------------------------------------------------------
# Active task
# ---------------------------------------------------------------------------

_PUSH_TIMEOUT_S = 0.5


def _push_active_task(workspace: str, task_id: str, title: str = "") -> None:
    """Best-effort POST to claude-hooks' running FastAPI server (claude-hooks
    task:996cc8f0), telling it what the active task now is. Pushing is a
    courtesy, not a dependency — this store is the durable source of truth
    regardless of whether that server is up, so tasks__set_active/clear_active
    must succeed identically whether or not this call lands. Swallows every
    failure (connection refused, timeout, DNS, non-2xx). Reads CLAUDE_HOOKS_URL
    fresh on every call rather than caching it at import time, so tests can
    point it at an unreachable port via monkeypatch.setenv.

    Logged at debug, not warning: firing with no claude-hooks server running
    is the expected common case, not an anomaly worth a human's attention.
    """
    base_url = os.environ.get("CLAUDE_HOOKS_URL", "http://127.0.0.1:8766")
    body = json.dumps({"workspace": workspace, "task_id": task_id, "title": title}).encode()
    req = urllib.request.Request(
        f"{base_url}/set-active-taskid",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=_PUSH_TIMEOUT_S).close()
    except (urllib.error.URLError, OSError, ValueError) as exc:
        log.debug("push to claude-hooks /set-active-taskid failed (non-fatal): %s", exc)


@_tool(hook=_loop_debt_hook)
def tasks__set_active(task_id: str) -> dict[str, Any]:
    """Set task_id as the active task for this workspace. In-memory only —
    not persisted, does not survive a restart (task:f5ace343).

    A single ephemeral pointer, not a stack: active status matters only
    while a task is being groomed, implemented, or introspected, so setting
    a different task simply replaces whichever one was active. Nothing here
    is destructive, so there is no confirm to pass. Re-setting the task
    already active is a no-op.
    """
    task = store().get(task_id)
    if task is None:
        return {"error": f"No task {task_id!r}"}
    scope = _scope()
    _set_and_broadcast(task_id, task.title, scope)
    return {"ok": True, "active": task_id, "scope": scope}


@_tool()
def tasks__active() -> dict[str, Any]:
    """The active task for this workspace, if any. In-memory only (task:f5ace343)."""
    scope = _scope()
    return {"active": store().get_active(scope), "scope": scope}


@_tool()
def tasks__clear_active() -> dict[str, Any]:
    """Clear the active task for this workspace, if any."""
    scope = _scope()
    _clear_and_broadcast(scope)
    return {"ok": True, "active": None, "scope": scope}


# ---------------------------------------------------------------------------
# Concepts
#
# `repo` is required rather than defaulting to a global, so these work against
# this repo or any other holding a concept_store/concepts.json. That explicit
# argument is exactly what made the equivalent tools reusable here, and it is
# why a store is never tied to whichever server happens to be running.
# ---------------------------------------------------------------------------

@_tool()
def concept__list(repo: str, module: str = "") -> dict[str, Any]:
    """Architectural concepts for a repo, optionally filtered to one module."""
    return {"concepts": ConceptStore(repo).list(module)}


@_tool()
def concept__get(repo: str, name: str) -> dict[str, Any]:
    """One concept by slug.

    {"found": bool, "concept": {...}} on both branches — not the concept
    fields flattened onto the response — so "found" is unambiguous rather than
    a key that happens to collide with a real field name, and so a caller
    checking result["found"] never has to also know whether this shape or a
    flat one answered the call. Matches the shape claude-hooks' own
    concept__get already established and several of its skills depend on by
    bare name (task:756c14db) — a second implementation with a different shape
    would have made removing that duplicate unsafe.
    """
    concept = ConceptStore(repo).get(name)
    if concept is None:
        return {"found": False, "error": f"No concept {name!r} in {repo}"}
    return {"found": True, "concept": concept}


@_tool()
def concept__modules(repo: str) -> dict[str, Any]:
    """Every module that has at least one concept."""
    return {"modules": ConceptStore(repo).modules()}


@_tool()
def concept__search(repo: str, query: str) -> dict[str, Any]:
    """Substring search over name, module, description, contracts, and invariants."""
    return {"concepts": ConceptStore(repo).search(query)}


@_tool()
def concept__upsert(repo: str, concept: dict) -> dict[str, Any]:
    """Insert or merge a concept.

    Merges rather than overwrites, so updating one field cannot silently drop
    invariants the caller did not mention. Requires name, module, description.
    `related` (other concept slugs) is capped at 3 entries — a merge that
    would overflow it is rejected rather than silently truncated.
    """
    try:
        merged = ConceptStore(repo).upsert(concept)
    except ValueError as exc:
        return {"error": str(exc)}
    return {"ok": True, "name": merged["name"], "module": merged["module"]}


@_tool()
def concept__delete(repo: str, name: str) -> dict[str, Any]:
    """Remove a concept. Returns deleted=False when it was not there."""
    return {"ok": True, "deleted": ConceptStore(repo).delete(name)}


@_tool()
def concept__uncovered(repo: str, modules: list[str]) -> dict[str, Any]:
    """Which of the given modules have no concept — the coverage check.

    Has no counterpart in the original tool surface. Added because coverage is
    the thing that actually lapses: a store stays accurate about what it
    describes while silently falling behind what exists.
    """
    return {"uncovered": ConceptStore(repo).uncovered(modules)}


# ---------------------------------------------------------------------------
# Loop memory
#
# Scoped to what introspection produces and nothing else can hold: a constraint
# discovered the hard way, a technique worth reusing, a recurring pitfall.
# Architectural facts belong in concept__*, task-specific reasoning in
# tasks__add_decision. This is neither, and must not grow into a general store.
# ---------------------------------------------------------------------------

@_tool()
def task_memory__record(slug: str, text: str, task_id: str, kind: str = "constraint") -> dict[str, Any]:
    """Record a lesson from a finished task. kind: constraint | technique | pitfall.

    `task_id` is required — a lesson with no evidence is an opinion, and the
    citation is what lets a later task confirm or contradict it.
    Re-recording an existing slug updates the text and adds the new source.
    """
    if store().get(task_id) is None:
        return {"error": f"No task {task_id!r} — a memory must cite a real task."}
    try:
        return {"ok": True, "memory": memory().record(slug, text, task_id, kind)}
    except Rejected as exc:
        return {"error": str(exc)}


@_tool()
def task_memory__recall(query: str = "", kind: str = "", limit: int = DEFAULT_RECALL_LIMIT,
                        include_superseded: bool = False) -> dict[str, Any]:
    """Search loop memories. Omit query to list the most recent.

    Superseded memories are excluded unless asked for: stale knowledge that
    keeps surfacing is worse than none, because it reads as current. Every
    result carries a derived `standing` — unverified, confirmed, disputed, or
    contradicted — so a disputed lesson is never returned as settled fact.
    """
    return {"memories": memory().recall(query, kind, limit, include_superseded)}


@_tool()
def task_memory__get(slug: str) -> dict[str, Any]:
    """One memory by slug, including its standing and every task linked to it."""
    found = memory().get(slug)
    return found or {"error": f"No memory {slug!r}"}


@_tool(hook=_stale_memory_hook)
def task_memory__link(slug: str, task_id: str, relation: str = "confirmed_by") -> dict[str, Any]:
    """Record that a task confirmed or contradicted a memory. Idempotent.

    relation: confirmed_by | contradicted_by | learned_from. This is the same
    feedback edge grooming has — a memory claims a lesson generalises, and
    later tasks are what grade that claim.
    """
    if store().get(task_id) is None:
        return {"error": f"No task {task_id!r}"}
    try:
        return {"ok": True, "created": memory().link(slug, task_id, relation),
                "memory": memory().get(slug)}
    except Rejected as exc:
        return {"error": str(exc)}


@_tool()
def task_memory__supersede(slug: str, by: str) -> dict[str, Any]:
    """Mark a memory obsolete, naming the memory that replaced it.

    The row survives. Obsolete knowledge is flagged rather than removed, and a
    lesson that stopped being true is itself evidence about how things changed.
    """
    try:
        return {"ok": True, "memory": memory().supersede(slug, by)}
    except Rejected as exc:
        return {"error": str(exc)}


@_tool()
def task_memory__forget(slug: str) -> dict[str, Any]:
    """Delete a memory outright — for one that was WRONG, not merely outdated.

    Outdated knowledge should be superseded, which keeps the trail. Use this
    only for a lesson that should never have been recorded.
    """
    return {"ok": True, "forgotten": memory().forget(slug)}


def main() -> None:
    log.info("taskfw MCP server starting scope=%s", _scope())
    mcp.run()


if __name__ == "__main__":
    main()
