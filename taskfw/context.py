"""tasks__context — the whole working bundle for a task, in one call.

THE CONTRACT, specified before implementation because this is the design the
project stands or falls on. With pull-only context there is no per-turn
injected block: this call is the sole replacement for it. Return too much and
the agent burns budget on every invocation; return too little and it works
context-blind with nothing to signal the gap.

SECTIONS, in fixed order. Order is part of the contract — an agent that reads
top-down should hit the most decision-relevant material first:

  1. task       the object itself. NEVER trimmed.
  2. decisions  why the task looks the way it does. Most recent first.
  3. grooming   findings from the last grooming pass.
  4. graph      parent, children, and edges — deterministic traversal.
  5. commits    what has actually landed, an exact per-task lookup.
  6. related    full-text neighbours. The only approximate section.

TRIM ORDER, applied in reverse of usefulness until the bundle fits CHAR_BUDGET:
related -> commits -> graph -> grooming -> decisions. The task itself is never
trimmed; a bundle without its task is useless rather than merely large.
Anything dropped is reported in `truncated`, so a caller can tell the
difference between "no commits" and "commits omitted for space".

VERBOSITY. `summary` is what a one-line pointer leads an agent to call first —
identity, status, progress, and the open checklist. `full` is for starting real
work.
"""
from __future__ import annotations

from taskfw.log import get_logger
from taskfw.models import Task
from taskfw.store import TaskStore

log = get_logger(__name__)

#: Total character budget for a full bundle. Characters rather than tokens
#: deliberately: no tokeniser dependency, and the cap only needs to be
#: approximately right to do its job.
CHAR_BUDGET = 12000

MAX_DECISIONS = 15
MAX_COMMITS = 20
MAX_RELATED = 5

#: Least useful first. Reversing this is how you decide what survives.
TRIM_ORDER = ("related", "commits", "graph", "grooming", "decisions")


def _task_summary(task: Task) -> dict:
    done, total = task.progress
    return {
        "id": task.id,
        "type": task.type,
        "status": task.status,
        "title": task.title,
        "parent": task.parent,
        "progress": {"done": done, "total": total},
        "open_items": [r.text for r in task.resolution if not r.done],
    }


def build_context(store: TaskStore, task_id: str, verbosity: str = "full") -> dict:
    """Assemble the bundle. Returns {"error": ...} for an unknown task."""
    task = store.get(task_id)
    if task is None:
        log.info("context task=%s NOT FOUND", task_id)
        return {"error": f"No task {task_id!r}"}

    if verbosity == "summary":
        log.debug("context task=%s verbosity=summary", task_id)
        return {"task": _task_summary(task), "verbosity": "summary"}

    bundle: dict = {
        "verbosity": "full",
        "task": {
            **_task_summary(task),
            "motivation": task.motivation,
            "resolution": [{"text": r.text, "done": r.done} for r in task.resolution],
            "files": task.files,
            "tags": task.tags,
            "notes": task.notes,
            "created_at": task.created_at,
            "updated_at": task.updated_at,
        },
        "decisions": [
            {"ts": e["ts"], "text": e["text"]}
            for e in store.events(task_id, limit=MAX_DECISIONS * 4)
            if e["kind"] == "decision"
        ][:MAX_DECISIONS],
        "grooming": task.grooming or {},
        "graph": _graph(store, task),
        "commits": store.commits(task_id)[:MAX_COMMITS],
        "related": _related(store, task),
    }

    truncated = _enforce_budget(bundle)
    if truncated:
        bundle["truncated"] = truncated
        log.info("context task=%s trimmed sections=%s", task_id, ",".join(truncated))
    log.debug("context task=%s size=%d sections=%d", task_id, _size(bundle), len(bundle))
    return bundle


def _graph(store: TaskStore, task: Task) -> dict:
    parent = store.get(task.parent) if task.parent else None
    return {
        "parent": _task_summary(parent) if parent else None,
        "children": [_task_summary(c) for c in store.children(task.id)],
        "edges": store.edges(task.id),
    }


def _related(store: TaskStore, task: Task) -> list[dict]:
    """Full-text neighbours, excluding the task itself.

    The only approximate section, and the first to be trimmed. Everything else
    in the bundle is an exact lookup — this is the one place a wrong answer is
    merely unhelpful rather than misleading.
    """
    if not task.title:
        return []
    # Quote the query so punctuation in a title cannot be read as FTS syntax.
    hits = store.search(f'"{task.title}"', limit=MAX_RELATED + 1)
    return [_task_summary(t) for t in hits if t.id != task.id][:MAX_RELATED]


def _size(bundle: dict) -> int:
    import json

    return len(json.dumps(bundle, default=str))


def _enforce_budget(bundle: dict) -> list[str]:
    """Drop whole sections, least useful first, until the bundle fits.

    Whole sections rather than partial truncation: a half-listed set of commits
    reads as complete and is worse than an absent one that says so.
    """
    dropped: list[str] = []
    for section in TRIM_ORDER:
        if _size(bundle) <= CHAR_BUDGET:
            break
        if bundle.get(section):
            bundle[section] = [] if isinstance(bundle[section], list) else {}
            dropped.append(section)
    return dropped
