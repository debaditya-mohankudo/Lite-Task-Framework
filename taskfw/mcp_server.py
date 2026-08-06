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
import os
from typing import Any, Callable

from mcp.server import MCPServer

from taskfw import dispatcher, lifecycle
from taskfw.accuracy import grooming_accuracy
from taskfw.concepts import ConceptStore
from taskfw.config import DEFAULT_RECALL_LIMIT
from taskfw.context import build_context, related_candidates
from taskfw.db.connect import connect
from taskfw.log import get_logger
from taskfw.memory import MemoryStore, Rejected
from taskfw.models import ResolutionItem, Task
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
    """Active-task scope. Per-workspace when there is one, else global."""
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


def _drift_reflection_hook(result: dict[str, Any]) -> None:
    """The periodic active-task nudge (dispatcher.drift_reflection_nudge).

    Recomputed fresh from global session state (_scope(), the active-task
    pointer) rather than anything the specific call did, so it composes onto
    any tool uniformly via dispatcher.combine. Wired onto reads and mutating
    tools alike; tasks__create, tasks__finish, and tasks__add_introspection
    don't carry it, matching the behavior this refactor preserved rather than
    widened (task:58782207).
    """
    scope = _scope()
    active_task_id = store().get_active(scope) or ""
    active_task = store().get(active_task_id) if active_task_id else None
    active_task_title = active_task.title if active_task else ""
    dispatcher.apply_nudge(
        result, "drift_reflection_nudge",
        dispatcher.drift_reflection_nudge(scope, active_task_id, active_task_title),
    )


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


def _introspection_hook(result: dict[str, Any]) -> None:
    """introspection_nudge, for tasks__add_introspection.

    introspection_nudge needs the report itself, which is one of the tool's
    arguments rather than anything in `result` — re-fetched as
    task.introspection[-1], the entry the tool's own body just appended and
    saved, rather than threading the argument through result just for this.
    """
    task = _refetch(result)
    if task and task.introspection:
        dispatcher.apply_nudge(
            result, "memory_nudge",
            dispatcher.introspection_nudge(task.introspection[-1], result["id"], store().conn),
        )


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

@_tool(hook=_drift_reflection_hook)
def tasks__context(task_id: str = "", verbosity: str = "full") -> dict[str, Any]:
    """The whole working bundle for a task: object, decisions, grooming, graph, commits, related.

    This is the main entry point — with no prompt injection, it is how an agent
    picks up a task's context. Omit task_id to use the active task.
    verbosity: "full" to start work, "summary" for identity and open items only.
    """
    task_id = task_id or store().get_active(_scope()) or ""
    if not task_id:
        return {"error": "No task_id given and no active task set."}
    return build_context(store(), task_id, verbosity)


@_tool()
def tasks__grooming_accuracy(limit: int = 25) -> dict[str, Any]:
    """How well grooming has been predicting, across recent finished tasks.

    The only tool that reads across tasks rather than into one. Grades live
    inside each task's grooming, so without this a pattern is writable and
    unreadable — and "repeated `wrong` means grooming asks the wrong questions"
    stays advice nobody can act on.

    Tallies are recomputed from the per-risk grades, never read from an
    introspection report's self-reported count.
    """
    return grooming_accuracy(store(), limit=limit)


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


@_tool(hook=_drift_reflection_hook)
def tasks__get(task_id: str) -> dict[str, Any]:
    """Return one task object."""
    task = store().get(task_id)
    return task.to_dict() if task else {"error": f"No task {task_id!r}"}


@_tool(hook=_drift_reflection_hook)
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
def tasks__list(status: str = "open,blocked", type: str = "", parent: str = "", limit: int = 50) -> list[dict]:
    """List tasks. status is comma-separated; empty means every status."""
    statuses = tuple(s.strip() for s in status.split(",") if s.strip()) or None
    tasks = store().list(status=statuses, type=type or None, parent=parent or None, limit=limit)
    return [
        {"id": t.id, "type": t.type, "status": t.status, "title": t.title,
         "parent": t.parent, "progress": list(t.progress)}
        for t in tasks
    ]


@_tool()
def tasks__search(query: str, limit: int = 25) -> list[dict]:
    """Full-text search over titles, motivation, notes, tags, files, and checklist items."""
    return [
        {"id": t.id, "type": t.type, "status": t.status, "title": t.title}
        for t in store().search(query, limit=limit)
    ]


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

@_tool()
def tasks__create(
    title: str,
    type: str = "task",
    parent: str = "",
    motivation: str = "",
    resolution: list[str] | None = None,
    files: list[str] | None = None,
    tags: list[str] | None = None,
    notes: str = "",
) -> dict[str, Any]:
    """Create a task or epic.

    Two types only: "epic" groups, "task" does work. resolution is a list of
    checklist item texts — there is no body template to satisfy and no required
    sections, because the object's shape is the schema.

    The response's related_candidates is advisory only — titles that share
    words with an existing task, surfaced for a human to review. Nothing here
    ever creates a tasks__link edge; that stays a deliberate, separate call.
    """
    task = Task(
        title=title, type=type, parent=parent or None, motivation=motivation,
        resolution=[ResolutionItem(t) for t in (resolution or [])],
        files=files or [], tags=tags or [], notes=notes,
    )
    parent_task = store().get(task.parent) if task.parent else None
    if task.parent and parent_task is None:
        return {"error": f"Parent {task.parent!r} does not exist."}
    decision = lifecycle.check_save(task, parent=parent_task)
    if not decision:
        return _denied(decision)
    store().save(task)
    result: dict[str, Any] = {"ok": True, "id": task.id, "type": task.type, "status": task.status}
    candidates = related_candidates(store(), task)
    if candidates:
        result["related_candidates"] = candidates
    return result


@_tool(hook=dispatcher.combine(_drift_reflection_hook, _finish_reminder_hook))
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
        updated.grooming = grooming

    parent_task = store().get(updated.parent) if updated.parent else None
    decision = lifecycle.check_save(updated, previous=current, parent=parent_task)
    if not decision:
        return _denied(decision)
    store().save(updated)
    return {"ok": True, "id": updated.id, "status": updated.status}


@_tool(hook=dispatcher.combine(_drift_reflection_hook, _finish_reminder_hook))
def tasks__check_item(task_id: str, index: int, done: bool = True) -> dict[str, Any]:
    """Tick or untick one resolution checklist item by its zero-based index."""
    task = store().get(task_id)
    if task is None:
        return {"error": f"No task {task_id!r}"}
    if not 0 <= index < len(task.resolution):
        return {"error": f"No item {index} — task has {len(task.resolution)}."}
    task.resolution[index].done = done
    store().save(task)
    d, total = task.progress
    return {"ok": True, "id": task_id, "progress": {"done": d, "total": total}}


@_tool(hook=_finish_hook)
def tasks__finish(task_id: str, reason: str = "") -> dict[str, Any]:
    """Mark a task done.

    Idempotent: finishing an already-done task succeeds rather than erroring,
    which follows from the same-status rule and makes retries safe. Finishing
    an abandoned task IS refused — abandoned is terminal and is not the state
    the caller asked for.

    See taskfw.dispatcher: when the task closes with no introspection report
    yet, the response carries a non-blocking `introspection_nudge` — the
    host-agnostic equivalent of a hook reminding you to run
    /task-introspection while the context is fresh.
    """
    task = store().get(task_id)
    if task is None:
        return {"error": f"No task {task_id!r}"}
    decision = lifecycle.check_transition(task.status, "done")
    if not decision:
        return _denied(decision)
    task.status = "done"
    store().save(task)
    if reason:
        store().add_event(task_id, reason, kind="status")
    scope = _scope()
    if store().get_active(scope) == task_id:
        store().clear_active(scope)
    return {"ok": True, "id": task_id, "status": "done"}


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


@_tool(hook=_drift_reflection_hook)
def tasks__add_decision(task_id: str, decision: str) -> dict[str, Any]:
    """Record a design decision. Surfaces in tasks__context, where it explains the task's shape."""
    if store().get(task_id) is None:
        return {"error": f"No task {task_id!r}"}
    store().add_event(task_id, decision, kind="decision")
    return {"ok": True, "id": task_id}


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------

@_tool()
def tasks__link(from_id: str, to_id: str, rel: str = "relates_to") -> dict[str, Any]:
    """Create an edge between two tasks. Idempotent."""
    for tid in (from_id, to_id):
        if store().get(tid) is None:
            return {"error": f"No task {tid!r}"}
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


@_tool(hook=_drift_reflection_hook)
def tasks__add_commit(task_id: str, sha: str, repo: str = "") -> dict[str, Any]:
    """Record that a commit implemented a task. Idempotent."""
    if store().get(task_id) is None:
        return {"error": f"No task {task_id!r}"}
    recorded = store().add_commit(task_id, sha, repo)
    return {"ok": True, "recorded": recorded}


# ---------------------------------------------------------------------------
# Active task
# ---------------------------------------------------------------------------

@_tool()
def tasks__set_active(task_id: str, confirm: bool = False) -> dict[str, Any]:
    """Set the active task for this workspace. Persisted, so it survives a restart.

    Switching away from a different active task is refused unless confirm=True
    — see lifecycle.check_active_switch. Activating for the first time, or
    re-setting the task that's already active, never requires confirm.
    """
    if store().get(task_id) is None:
        return {"error": f"No task {task_id!r}"}
    scope = _scope()
    previous = store().get_active(scope)
    decision = lifecycle.check_active_switch(previous, task_id, confirm)
    if not decision:
        return _denied(decision)
    store().set_active(task_id, scope)
    return {"ok": True, "active": task_id, "scope": scope}


@_tool()
def tasks__active() -> dict[str, Any]:
    """The active task for this workspace, if any."""
    task_id = store().get_active(_scope())
    return {"active": task_id, "scope": _scope()}


@_tool()
def tasks__clear_active() -> dict[str, Any]:
    """Clear the active task for this workspace."""
    store().clear_active(_scope())
    return {"ok": True, "scope": _scope()}


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
