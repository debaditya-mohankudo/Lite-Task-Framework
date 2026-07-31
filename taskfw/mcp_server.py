"""MCP server — the portable interface to the framework.

Any MCP host reaches the whole framework through these tools; MCP is the
portability layer, so there is no neutral abstraction on top of it.

Tools are thin. Each one validates through taskfw.lifecycle and then calls
taskfw.store, so there is exactly one implementation of every rule, shared with
the optional hooks. A tool cannot enforce a different set than a hook because
neither owns the rules.
"""
from __future__ import annotations

import os
from typing import Any

from mcp.server import MCPServer

from taskfw import lifecycle
from taskfw.context import build_context
from taskfw.log import get_logger
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
    global _store
    _store = s


def _scope() -> str:
    """Active-task scope. Per-workspace when there is one, else global."""
    return os.environ.get("TASKFW_SCOPE") or os.getcwd()


def _denied(d: lifecycle.Decision) -> dict:
    return {"error": d.reason, "rule": d.rule}


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

@mcp.tool()
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


@mcp.tool()
def tasks__get(task_id: str) -> dict[str, Any]:
    """Return one task object."""
    task = store().get(task_id)
    return task.to_dict() if task else {"error": f"No task {task_id!r}"}


@mcp.tool()
def tasks__list(status: str = "open,blocked", type: str = "", parent: str = "", limit: int = 50) -> list[dict]:
    """List tasks. status is comma-separated; empty means every status."""
    statuses = tuple(s.strip() for s in status.split(",") if s.strip()) or None
    tasks = store().list(status=statuses, type=type or None, parent=parent or None, limit=limit)
    return [
        {"id": t.id, "type": t.type, "status": t.status, "title": t.title,
         "parent": t.parent, "progress": list(t.progress)}
        for t in tasks
    ]


@mcp.tool()
def tasks__search(query: str, limit: int = 25) -> list[dict]:
    """Full-text search over titles, motivation, notes, tags, files, and checklist items."""
    return [
        {"id": t.id, "type": t.type, "status": t.status, "title": t.title}
        for t in store().search(query, limit=limit)
    ]


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

@mcp.tool()
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
    return {"ok": True, "id": task.id, "type": task.type, "status": task.status}


@mcp.tool()
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
    than ambiguous across one blob — the system this replaces had a `body`
    parameter whose replace semantics were easy to misread as appending, which
    cost real data.
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


@mcp.tool()
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


@mcp.tool()
def tasks__finish(task_id: str, reason: str = "") -> dict[str, Any]:
    """Mark a task done.

    Idempotent: finishing an already-done task succeeds rather than erroring,
    which follows from the same-status rule and makes retries safe. Finishing
    an abandoned task IS refused — abandoned is terminal and is not the state
    the caller asked for.
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
    return {"ok": True, "id": task_id, "status": "done"}


@mcp.tool()
def tasks__add_decision(task_id: str, decision: str) -> dict[str, Any]:
    """Record a design decision. Surfaces in tasks__context, where it explains the task's shape."""
    if store().get(task_id) is None:
        return {"error": f"No task {task_id!r}"}
    store().add_event(task_id, decision, kind="decision")
    return {"ok": True, "id": task_id}


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------

@mcp.tool()
def tasks__link(from_id: str, to_id: str, rel: str = "relates_to") -> dict[str, Any]:
    """Create an edge between two tasks. Idempotent."""
    for tid in (from_id, to_id):
        if store().get(tid) is None:
            return {"error": f"No task {tid!r}"}
    return {"ok": True, "created": store().link(from_id, to_id, rel)}


@mcp.tool()
def tasks__unlink(from_id: str, to_id: str, rel: str = "") -> dict[str, Any]:
    """Remove an edge, or every edge between two tasks when rel is omitted."""
    return {"ok": True, "removed": store().unlink(from_id, to_id, rel or None)}


@mcp.tool()
def tasks__edges(task_id: str) -> dict[str, Any]:
    """Edges touching a task, both directions."""
    return store().edges(task_id)


@mcp.tool()
def tasks__add_commit(task_id: str, sha: str, repo: str = "") -> dict[str, Any]:
    """Record that a commit implemented a task. Idempotent."""
    if store().get(task_id) is None:
        return {"error": f"No task {task_id!r}"}
    return {"ok": True, "recorded": store().add_commit(task_id, sha, repo)}


# ---------------------------------------------------------------------------
# Active task
# ---------------------------------------------------------------------------

@mcp.tool()
def tasks__set_active(task_id: str) -> dict[str, Any]:
    """Set the active task for this workspace. Persisted, so it survives a restart."""
    if store().get(task_id) is None:
        return {"error": f"No task {task_id!r}"}
    store().set_active(task_id, _scope())
    return {"ok": True, "active": task_id, "scope": _scope()}


@mcp.tool()
def tasks__active() -> dict[str, Any]:
    """The active task for this workspace, if any."""
    task_id = store().get_active(_scope())
    return {"active": task_id, "scope": _scope()}


@mcp.tool()
def tasks__clear_active() -> dict[str, Any]:
    """Clear the active task for this workspace."""
    store().clear_active(_scope())
    return {"ok": True, "scope": _scope()}


def main() -> None:
    log.info("taskfw MCP server starting scope=%s", _scope())
    mcp.run()


if __name__ == "__main__":
    main()
