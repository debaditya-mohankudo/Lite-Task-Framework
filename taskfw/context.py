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
  6. related    full-text neighbours. Approximate.
  7. lessons    loop memories matching this task. Approximate.

The last two are the approximate sections; everything above them is an exact
lookup, where a wrong answer would be misleading rather than merely unhelpful.

WHY LESSONS IS HERE AT ALL. Introspection records constraints and techniques
into loop memory, and doc 05 says plainly to read them back when grooming.
Until this section existed there was no guaranteed read path: a lesson
surfaced only if an agent independently remembered to call
`task_memory__recall`, which made the whole subsystem a diary — a write path
with no reader. That also starved the confirmed_by/contradicted_by grading
edge, because a memory nobody recalls is a memory nobody ever tests.

This is still a pull, not a push. Nothing arrives unasked; the agent called
tasks__context, and the lessons come back inside the bundle it asked for.

TRIM ORDER, applied in reverse of usefulness until the bundle fits CHAR_BUDGET:
related -> lessons -> commits -> graph -> grooming -> decisions. The task
itself is never trimmed; a bundle without its task is useless rather than
merely large. Lessons sits second because it is approximate like related, but
ahead of it in value: a weak related task is a bad pointer, while a lesson
that survived grading is knowledge that cost a whole task to learn.
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
from taskfw.memory import MemoryStore
from taskfw.models import Task
from taskfw.store import TaskStore

log = get_logger(__name__)

#: Total character budget for a full bundle. Characters rather than tokens
#: deliberately: no tokeniser dependency, and the cap only needs to be
#: approximately right to do its job.
CHAR_BUDGET = 12000

MAX_DECISIONS = 15
MAX_COMMITS = 5
MAX_RELATED = 5
MAX_EDGES = 5  # per direction

#: Lower than MAX_RELATED on purpose. Neither section has a relevance floor —
#: both fill up to their cap with the best available matches however weak — but
#: the consequences differ. A weak related task reads as a pointer the agent can
#: ignore; a weak lesson reads as advice, and advice that does not apply is
#: worse than none. Fewer slots is the cheapest available brake.
MAX_LESSONS = 3

#: Least useful first. Reversing this is how you decide what survives.
TRIM_ORDER = ("related", "lessons", "commits", "graph", "grooming", "decisions")


def _query_terms(task: Task) -> str:
    """The words that describe a task, for any full-text lookup about it.

    One definition, because both approximate sections of the bundle
    (related_candidates, lessons_for) search on it. Two copies would drift the
    moment either one learned a new term, and the divergence would only ever
    surface as an inconsistent bundle.
    """
    return " ".join([task.title, *task.tags]).strip()


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


def build_context(store: TaskStore, task_id: str, verbosity: str = "full",
                  memory: MemoryStore | None = None) -> dict:
    """Assemble the bundle. Returns {"error": ...} for an unknown task.

    `memory` is optional because memories live in the same database as tasks,
    so one can always be opened from store.conn. Callers that already hold the
    shared instance should pass it: MemoryStore.__init__ runs a CREATE VIRTUAL
    TABLE IF NOT EXISTS and commits, and doing that on every context call would
    put a write on a pure read path.
    """
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
        "lessons": lessons_for(store, task, memory),
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

    Query terms are title words plus tags, not title alone — tags are the
    hand-curated signal store.search()'s own scoring weights 3:1 over body
    text, so leaving them out of the query meant two tasks sharing only tags
    (no title overlap) could never surface as related to each other.
    """
    query = _query_terms(task)
    if not query:
        return []
    # store.search() builds its own OR-of-terms query internally — passing
    # plain words (not pre-formatted FTS5 syntax) is what makes term overlap
    # work instead of requiring an exact phrase.
    hits = store.search(query, limit=MAX_RELATED + 1)
    return [_task_summary(t) for t in hits if t.id != task.id][:MAX_RELATED]


def lessons_for(store: TaskStore, task: Task,
                memory: MemoryStore | None = None) -> list[dict]:
    """Loop memories matching this task, best match first.

    The bundle's second approximate section, and the read path that makes loop
    memory load-bearing rather than write-only.

    QUERY SHAPE comes from _query_terms, shared with related_candidates so the
    notion of "what is this task about" has one definition. The justification does not
    transfer intact, though, and pretending otherwise would be the kind of
    unexamined premise this project treats as the hazard: related_candidates
    leans on store.search() weighting tag overlap 3:1 over body text, whereas
    memories_fts indexes only `slug text` — it has no tags column — and ranks by
    plain bm25. So tags are a weighted, hand-curated signal on the related side
    and merely extra OR-ed words here. They are kept because a task tagged
    `memory` should still reach a lesson about memory, not because the ranking
    behaves the same way.

    COUNTING IS OFF. recall(count_hit=False) because hit_count and last_hit
    answer "which lessons does anyone deliberately reach for", and bundle
    assembly is not a deliberate reach. See MemoryStore.recall.

    Superseded memories are already excluded by recall's default, and each row
    arrives carrying its derived `standing`, so a disputed lesson is handed
    over marked disputed rather than as settled fact. Nothing is recomputed
    here — standing is derived in the memory store and has one home.
    """
    query = _query_terms(task)
    if not query:
        return []
    store_ = memory if memory is not None else MemoryStore(conn=store.conn)
    return store_.recall(query, limit=MAX_LESSONS, count_hit=False)


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
