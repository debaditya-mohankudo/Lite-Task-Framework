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

Edges within `graph` are additionally capped at MAX_EDGES per direction,
independent of CHAR_BUDGET — a task can accumulate far more edges than are
useful to read at once. When that cap drops edges, the counts are reported in
`edges_truncated` ({"outgoing": N, "incoming": N}), a field distinct from
`truncated`: `truncated` means CHAR_BUDGET dropped a whole section, while
`edges_truncated` means the section is present but its edges list was capped.

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
MAX_EDGES = 5  # per direction

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

    graph, edges_dropped = _graph(store, task)
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
        "graph": graph,
        "commits": store.commits(task_id)[:MAX_COMMITS],
        "related": related_candidates(store, task),
    }
    if edges_dropped:
        bundle["edges_truncated"] = edges_dropped

    truncated = _enforce_budget(bundle)
    if truncated:
        bundle["truncated"] = truncated
        log.info("context task=%s trimmed sections=%s", task_id, ",".join(truncated))
    log.debug("context task=%s size=%d sections=%d", task_id, _size(bundle), len(bundle))
    return bundle


def _graph(store: TaskStore, task: Task) -> dict:
    parent = store.get(task.parent) if task.parent else None
    edges = store.edges(task.id)
    out, inc = edges["outgoing"], edges["incoming"]
    graph = {
        "parent": _task_summary(parent) if parent else None,
        "children": [_task_summary(c) for c in store.children(task.id)],
        "edges": {"outgoing": out[:MAX_EDGES], "incoming": inc[:MAX_EDGES]},
    }
    dropped = {
        k: v
        for k, v in {"outgoing": len(out) - MAX_EDGES, "incoming": len(inc) - MAX_EDGES}.items()
        if v > 0
    }
    return graph, dropped


def related_candidates(store: TaskStore, task: Task) -> list[dict]:
    """Full-text neighbours, excluding the task itself.

    Public rather than a module-private helper: build_context is not the only
    caller — mcp_server.tasks__create uses this too, to surface candidates at
    creation time. A leading-underscore name reached from outside its own
    module is a signal the behaviour deserves a shared name, not a private one
    imported across the boundary anyway.

    The only approximate section of tasks__context's bundle, and the first to
    be trimmed there. Everything else in the bundle is an exact lookup — this
    is the one place a wrong answer is merely unhelpful rather than misleading.
    """
    if not task.title:
        return []
    hits = store.search(_title_or_query(task.title), limit=MAX_RELATED + 1)
    return [_task_summary(t) for t in hits if t.id != task.id][:MAX_RELATED]


def _title_or_query(title: str) -> str:
    """OR the title's terms instead of matching it as one exact phrase.

    A whole-title phrase query (the previous approach) requires every word to
    appear, consecutively, in that exact order, in another task's indexed
    text — titles are long, specific, near-unique sentences, so that can
    practically never match anything but a near-duplicate title. Each term is
    quoted individually so punctuation in a title is read as literal text,
    never as an FTS5 operator.
    """
    terms = title.split()
    quoted = ['"{}"'.format(t.replace('"', '""')) for t in terms]
    return " OR ".join(quoted)


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
            if section == "graph":
                bundle.pop("edges_truncated", None)
    return dropped
