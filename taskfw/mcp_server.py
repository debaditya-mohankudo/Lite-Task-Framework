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
from datetime import datetime, timezone
from typing import Any, Callable

from mcp.server import MCPServer

from taskfw import dispatcher, lifecycle
from taskfw import scope as scope_mod
from taskfw.accuracy import _task_grading, grooming_accuracy, loop_debt
from taskfw.concepts import ConceptStore
from taskfw.config import DEFAULT_RECALL_LIMIT
from taskfw.context import TaskContext
from taskfw.db.connect import connect
from taskfw.log import get_logger
from taskfw.memory import MemoryStore, Rejected
from taskfw.risk import coerce, normalise_text
from taskfw.scope import derive as derive_scope, for_repo as scope_for_repo
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


def _workspace() -> str:
    """Active-task workspace. Per-workspace when there is one, else global.

    NOT the same thing as `Task.scope`, and deliberately still a raw path.
    This keys an in-memory pointer at "which task am I working on right now",
    which is ephemeral and per-*directory*: two worktrees of one repo are two
    places someone can be working, and each deserves its own active task.
    `derive_scope()` would collapse them onto one, which is the right answer
    for "which project owns this task" and the wrong one for "which task is
    open in this window". Two different questions, so two different functions,
    and two different names: this was `_scope()` and its responses said
    `scope`, the same key tasks__grooming_accuracy uses for a Scope value
    (review 2026-09-14 A4, task:a8394f74).
    """
    return os.environ.get("TASKFW_SCOPE") or os.getcwd()


def _denied(d: lifecycle.Ruling) -> dict:
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

    `_finish_hook` is not one of the hooks a tool opts into: every tool gets
    it, after its own hook (task:7097f9d4). It keys off a `finished` marker in
    the result, not a tool name, so wiring it per tool only added a way for a
    new tool that closes a task to forget it and skip the introspection
    reminder. On any other result it is one dict lookup.
    """
    post = dispatcher.combine(hook, _finish_hook) if hook else _finish_hook

    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            with dispatcher.tool_called(fn.__name__, post=post) as call:
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
        dispatcher.apply_nudge(result, dispatcher.finish_reminder_nudge, task)


def _ungroomed_progress_hook(result: dict[str, Any]) -> None:
    """ungroomed_progress_nudge, for tasks__update/tasks__check_item."""
    task = _refetch(result)
    if task:
        dispatcher.apply_nudge(result, dispatcher.ungroomed_progress_nudge, task)


def _finish_hook(result: dict[str, Any]) -> None:
    """finish_nudge, on any call whose result says it moved a task to done.

    Attached to every tool by _tool itself, not opted into per tool
    (task:7097f9d4). Keyed off `finished` in the result rather than a tool
    name, because four tools can close a task — tasks__finish, check_item's and add_decision's
    auto-finish on the last item, and tasks__update(status="done") — and a
    hook pinned to one of them left the other three closing silently
    (task:c0c5ff5f; the same failure class as task:1105f979's activation
    hook). `finished` is set only on the transition itself (see _finished),
    so editing an already-done task never re-fires it.
    """
    if not result.get("finished"):
        return
    task = _refetch(result)
    if task:
        dispatcher.apply_nudge(result, dispatcher.finish_nudge, task)


def _stale_memory_hook(result: dict[str, Any]) -> None:
    """stale_memory_nudge, for task_memory__link.

    The memory is already in `result["memory"]` (task_memory__link's own
    return shape re-fetches it after linking), so no separate lookup is
    needed the way the task hooks above need _refetch.
    """
    memory = result.get("memory")
    if memory:
        dispatcher.apply_nudge(result, dispatcher.stale_memory_nudge, memory)


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
        result, dispatcher.loop_debt_nudge,
        debt["skipped_introspection"], debt["tasks_examined"],
    )
    _, task_ungraded, _ = _task_grading(task)
    dispatcher.apply_nudge(result, dispatcher.task_debt_nudge, task.id, task_ungraded)


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
            result, dispatcher.introspection_nudge,
            task.introspection[-1], result["id"], store().conn,
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
    task_id = task_id or store().get_active(_workspace()) or ""
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

    `scope` is empty for global, a repository path ("." included), or a
    Scope value a task already carries (`git:...`, as tasks__context shows).
    """
    # Global by default: the aggregate is a deliberate, occasional read, and
    # a cross-project pattern in it is a real finding rather than noise.
    # for_repo, not derive, so a Scope value is never re-read as a directory
    # (task:2a7eacc8). Either way the result names the scope it counted.
    return grooming_accuracy(store(), limit=limit,
                             scope=scope_for_repo(scope) if scope else None)


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
    the pointer again: it lives until tasks__add_introspection files a report
    against it (task:2d24165a), which the task-introspection skill does at its
    final step — the sole deactivation path.

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
    ruling = lifecycle.check_save(task, parent=parent_task)
    if not ruling:
        return _denied(ruling)
    store().save(task)
    workspace = _workspace()
    store().set_active(task.id, workspace)
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


def _keep_grade(current: dict | None, incoming: dict) -> dict:
    """`incoming`, carrying `current`'s grade when incoming omits one.

    A non-empty incoming grade always wins — that is a deliberate re-grade.
    Only an absent or empty one is filled in, because a caller restating a
    risk's text did not mean to erase introspection's verdict on it.
    """
    if current and current.get("graded") and not incoming.get("graded"):
        incoming = dict(incoming)
        incoming["graded"] = current["graded"]
    return incoming


def _pop_text_match(pool: list, text: str, text_of: Callable[[Any], str]) -> Any | None:
    """Remove and return the first entry of `pool` whose text matches `text`, or None.

    Matching is by normalise_text, and an empty key matches nothing. Removing
    the match is what makes it consume-once: two identical incoming texts pair
    with two pool entries rather than both claiming one. The one home for
    this rule, shared by legacy-risk matching and checklist tick keeping.
    """
    key = normalise_text(text)
    if not key:
        return None
    for i, cand in enumerate(pool):
        if normalise_text(text_of(cand)) == key:
            return pool.pop(i)
    return None


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
      and, exactly as with an id match, keeps its grade when the incoming
      entry omits one (task:2a7eacc8: this path used to drop it). When
      nothing matches it falls back to the same carry-forward-if-graded
      rule. Stored grooming from before this change is never rewritten.

    Both match paths keep a grade through `_keep_grade`, the one home for
    that rule.
    """
    current = [coerce(r) for r in (current_raw or [])]
    incoming = [coerce(r) for r in (incoming_raw or [])]

    current_by_id = {r["id"]: r for r in current if r.get("id")}
    unmatched_idless = [r for r in current if not r.get("id")]
    consumed_ids: set[str] = set()

    merged: list[dict] = []
    for risk in incoming:
        rid = risk.get("id")
        if rid:
            merged.append(_keep_grade(current_by_id.get(rid), risk))
            consumed_ids.add(rid)
            continue
        match = _pop_text_match(unmatched_idless, risk.get("text", ""), lambda r: r.get("text", ""))
        new_entry = dict(risk)
        new_entry["id"] = new_id()
        merged.append(_keep_grade(match, new_entry))

    # Carry forward graded risks the incoming payload dropped by omission.
    for rid, risk in current_by_id.items():
        if rid not in consumed_ids and risk.get("graded"):
            merged.append(risk)
    for risk in unmatched_idless:
        if risk.get("graded"):
            new_entry = dict(risk)
            new_entry["id"] = new_id()
            merged.append(new_entry)

    return merged


def _keep_ticks(current: list[ResolutionItem], texts: list[str]) -> list[ResolutionItem]:
    """A replacement checklist from `texts`, keeping `done` on items restated unchanged.

    Rewording or appending to a checklist is not a statement that finished
    work became unfinished, so an item whose normalised text matches a
    current one keeps that item's tick (task:c0c5ff5f). Each current item is
    consumed once, so two identical texts pair with two current items rather
    than both inheriting one tick. Anything unmatched starts not-done — a
    new item has no evidence of being finished.
    """
    remaining = list(current)
    out: list[ResolutionItem] = []
    for text in texts:
        match = _pop_text_match(remaining, text, lambda c: c.text)
        out.append(ResolutionItem(text, done=bool(match and match.done)))
    return out


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
    scope: str = "",
) -> dict[str, Any]:
    """Update a task. Only the fields you pass are changed.

    Every field is replace-not-append, and that is explicit per field rather
    than ambiguous across one blob. A single free-text `body` argument makes
    replace-versus-append something the caller has to infer from prose, and
    inferring it wrong destroys content silently; naming each field makes the
    semantics visible at the call site instead.

    `scope` corrects which project a task belongs to, in the form
    taskfw.scope produces (`git:<host>/<path>`, `path:<abs>` or `hint:<text>`).
    Empty leaves it unchanged, so it cannot be cleared here. This is the one
    retroactive path to a scope (task:2110cf4c), and Task.scope's comment says
    why that is a different kind of fact from a derived one — so a change
    leaves a note event naming old and new, the only provenance it will have.
    """
    current = store().get(task_id)
    if current is None:
        return {"error": f"No task {task_id!r}"}
    if scope and not scope.startswith((scope_mod.GIT, scope_mod.PATH, scope_mod.HINT)):
        return {"error": f"scope {scope!r} must start with git:, path: or hint: — "
                         "see taskfw.scope; a bare string is indistinguishable from a fallback"}

    updated = Task.from_dict(current.to_dict())
    if scope:
        updated.scope = scope
    if title:
        updated.title = title
    if status:
        updated.status = status
    if motivation:
        updated.motivation = motivation
    if notes:
        updated.notes = notes
    if resolution is not None:
        updated.resolution = _keep_ticks(current.resolution, resolution)
    if files is not None:
        updated.files = files
    if tags is not None:
        updated.tags = tags
    if grooming is not None:
        merged_grooming = dict(grooming)
        current_risks = (current.grooming or {}).get("risks")
        # An absent `risks` key says nothing about risks, so they stay as
        # they are; only an explicit list (including []) is a re-groom of
        # them. Treating absence as [] retracted every ungraded risk on a
        # payload that never mentioned risks at all (task:c0c5ff5f).
        if "risks" in grooming:
            merged_grooming["risks"] = _merge_grooming_risks(current_risks, grooming["risks"])
        elif current_risks:
            merged_grooming["risks"] = current_risks
        updated.grooming = merged_grooming

    parent_task = store().get(updated.parent) if updated.parent else None
    ruling = lifecycle.check_save(updated, previous=current, parent=parent_task)
    if not ruling:
        return _denied(ruling)
    store().save(updated)
    if updated.scope != current.scope:
        store().add_event(
            task_id, f"scope corrected: {current.scope or '(unscoped)'} -> {updated.scope}"
        )
    if updated.status == "done" and current.status != "done":
        # Same record _finish_task leaves: a status event and the `finished`
        # marker _finish_hook keys off. Without them this path closed a task
        # with neither a trace in the event log nor an introspection reminder.
        return _finished(task_id, "status set to done via tasks__update", transitioned=True)
    return {"ok": True, "id": updated.id, "status": updated.status}


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
    active through introspection; tasks__add_introspection — the last pass of
    the loop — is the only thing that deactivates it (task:2d24165a).
    """
    task = store().get(task_id)
    if task is None:
        return {"error": f"No task {task_id!r}"}
    ruling = lifecycle.check_transition(task.status, "done")
    if not ruling:
        return _denied(ruling)
    was_done = task.status == "done"
    task.status = "done"
    store().save(task)
    return _finished(task_id, reason, transitioned=not was_done)


def _finished(task_id: str, reason: str, *, transitioned: bool) -> dict[str, Any]:
    """What a call that left a task done records and returns — the one home
    for both, shared by _finish_task and tasks__update's status->done branch.

    Writes `reason` as a status event when given. `finished: True` is present
    only when THIS call moved the task to done, which is what _finish_hook
    keys off. A repeat finish, or any later edit of a done task, carries no
    marker and so no finish_nudge.
    """
    if reason:
        store().add_event(task_id, reason, kind="status")
    result: dict[str, Any] = {"ok": True, "id": task_id, "status": "done"}
    if transitioned:
        result["finished"] = True
    return result


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
            if finish_result.get("finished"):
                result["finished"] = True
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


@_tool()
def tasks__finish(task_id: str, reason: str = "") -> dict[str, Any]:
    """Mark a task done.

    See taskfw.dispatcher: when the task closes with no introspection report
    yet, the response carries a non-blocking `finish_nudge` — the
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
    carries a non-blocking `introspection_nudge` so the omission is visible instead
    of silent.

    Recording a report also CLEARS the active pointer when the reported task
    is the one currently active (task:2d24165a) — introspection is the last
    pass of the loop, so deactivation belongs here, symmetric with
    tasks__create setting the pointer. This is the sole deactivation path;
    there is no separate tasks__clear_active tool to remember. `active_cleared`
    in the result says whether it happened — false when some other task (or
    none) was active, which is not an error. The pointer is in-memory, so a
    report filed against a non-active task simply leaves whatever was active
    in place, and it self-heals on the next tasks__create / tasks__set_active.
    """
    task = store().get(task_id)
    if task is None:
        return {"error": f"No task {task_id!r}"}
    task.introspection.append(report)
    store().save(task)
    workspace = _workspace()
    active_cleared = store().get_active(workspace) == task_id
    if active_cleared:
        store().clear_active(workspace)
    return {
        "ok": True,
        "id": task_id,
        "reports": len(task.introspection),
        "active_cleared": active_cleared,
    }


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
    dispatcher.apply_nudge(result, dispatcher.finish_reminder_nudge, task)
    dispatcher.apply_nudge(result, dispatcher.ungroomed_progress_nudge, task)


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
    ruling = lifecycle.check_link_rel(rel)
    if not ruling:
        return _denied(ruling)
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
    workspace = _workspace()
    store().set_active(task_id, workspace)
    return {"ok": True, "active": task_id, "workspace": workspace}


@_tool()
def tasks__active() -> dict[str, Any]:
    """The active task for this workspace, if any. In-memory only (task:f5ace343)."""
    workspace = _workspace()
    return {"active": store().get_active(workspace), "workspace": workspace}


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
    log.info("taskfw MCP server starting workspace=%s", _workspace())
    mcp.run()


if __name__ == "__main__":
    main()
