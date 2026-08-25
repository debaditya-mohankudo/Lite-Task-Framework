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

GROOMING IS THE ONE EXCEPTION to whole-section dropping. Every other trimmed
section is a list, where "drop the whole thing" and "keep only the best
entries" cost about the same in wasted budget. Grooming is a dict, and it is
typically the largest section by far — dropping it whole to save a few
thousand characters routinely left most of CHAR_BUDGET unspent (see
task:1c31a060). So grooming is trimmed field-by-field instead, least useful
first (GROOMING_TRIM_ORDER), until the bundle fits or grooming has nothing
left to give. Fields actually dropped are reported in `grooming_truncated` —
distinct from `truncated`, which still means the whole section is gone. Only
if grooming empties out entirely does it fall through to a normal whole-section
drop and get named in `truncated` like everything else.

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

import re

from taskfw.log import get_logger
from taskfw.memory import MemoryStore
from taskfw.task import Task
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

#: Lower than MAX_RELATED on purpose — a volume limit, not a relevance
#: mechanism (that distinction matters: task:60ebca8e's introspection graded
#: this cap ineffective as a relevance brake before _passes_floor existed,
#: because a cap bounds count and only a floor bounds quality). Kept lower
#: anyway because the consequences of a weak match still differ even after
#: both sections carry a floor: a weak related task reads as a pointer the
#: agent can ignore, while a weak lesson reads as advice, and inapplicable
#: advice is worse than none.
MAX_LESSONS = 3

#: Least useful first. Reversing this is how you decide what survives.
TRIM_ORDER = ("related", "lessons", "commits", "graph", "grooming", "decisions")

#: grooming's own field-drop order, least useful first — see "GROOMING IS THE
#: ONE EXCEPTION" above. clarifications last: they are verified facts, the
#: highest-value content grooming produces. open_questions next-to-last: an
#: unresolved blocking question is exactly what a caller starting work needs
#: to see. The rest are supplementary context, safe to shed first.
GROOMING_TRIM_ORDER = (
    "suggested_improvements", "prior_art", "hidden_assumptions",
    "risks", "open_questions", "clarifications",
)

#: The shape of a full-verbosity bundle, in section order — see the module
#: docstring's SECTIONS list. build_context copies this fresh (dict(...)) and
#: fills in every field on each call; nothing here is ever mutated or reused
#: across calls. Deliberately excludes edges_truncated, truncated, and
#: grooming_truncated: those three are conditional, added only when trimming
#: actually happens, so a key present here would claim they are always part
#: of the shape when they are not.
_BUNDLE_SKELETON: dict = {
    "verbosity": "full",
    "task": None,
    "decisions": [],
    "grooming": {},
    "graph": {},
    "commits": [],
    "related": [],
    "lessons": [],
}


def _query_terms(task: Task) -> str:
    """The words that describe a task, for any full-text lookup about it.

    One definition, because both approximate sections of the bundle
    (related_candidates, lessons_for) search on it. Two copies would drift the
    moment either one learned a new term, and the divergence would only ever
    surface as an inconsistent bundle.
    """
    return " ".join([task.title, *task.tags]).strip()


#: Below this length a word carries too little meaning to anchor a relevance
#: floor ("the", "is", "for") — kept as a length cutoff rather than a curated
#: stopword list, because a list needs maintaining and a length rarely does.
_MIN_TERM_LEN = 4

_WORD_RE = re.compile(r"[a-z0-9]+")


def _term_set(text: str) -> set[str]:
    """Meaningful lowercase words in `text`, for the term-overlap floor.

    Purely-numeric tokens are dropped even at full length: a tag like
    `introspection-2026-08-23` should contribute "introspection", not "2026" —
    every task and memory recorded on the same day would otherwise share that
    token and pass the floor on a date collision that means nothing.
    """
    return {
        w for w in _WORD_RE.findall(text.lower())
        if len(w) >= _MIN_TERM_LEN and not w.isdigit()
    }


def _passes_floor(query_terms: str, candidate_text: str) -> bool:
    """The relevance floor shared by related_candidates and lessons_for.

    related_candidates and lessons_for rank by relevance but never excluded
    anything, so both always filled up to their cap with the best available
    match among ALL rows in the store, however weak — confirmed live
    (task:60ebca8e) surfacing candidates with zero title or tag overlap with
    the task asking. Ranking orders candidates; it does not reject them, and
    only a floor does.

    TERM-OVERLAP MINIMUM, not a score threshold or a cutoff relative to the
    top hit — chosen because it is the one form that applies unchanged to
    both call sites. related_candidates ranks via store.search()'s
    _combination_score (tag overlap weighted 3:1 over body); lessons_for
    ranks via memories_fts's plain bm25 over a schema with no tags column at
    all. Neither score is comparable to the other or stable as the store
    grows, so a threshold or relative cutoff tuned against one would need
    separate recalibration for the other and would still drift as the corpus
    changes shape. A raw term-overlap check reads the same two strings
    (`query_terms`, `candidate_text`) regardless of which scorer ranked the
    candidate, so it is the only option that needed writing once.

    Requires at least one shared word of `_MIN_TERM_LEN`+ letters. If the
    query itself has no such word (a very short title, no tags), there is no
    vocabulary to test candidates against — the floor is permissive, not
    rejecting, in that case, since it has nothing but noise to filter on.
    """
    q = _term_set(query_terms)
    if not q:
        return True
    return bool(q & _term_set(candidate_text))


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


class TaskContext:
    """The interface callers depend on instead of build_context/related_candidates
    directly.

    mcp_server used to import and call those two free functions itself,
    passing `store` (and `memory`, for the full bundle) into each one
    individually. That is procedural coupling: every call site has to know
    which function needs which arguments, so a change to either function's
    signature is a change at every call site too. Binding `store`/`memory`
    once, here, and exposing them as named methods means build_context and
    related_candidates can gain, drop, or reorder parameters — or stop being
    the implementation entirely — without mcp_server changing at all; it only
    depends on `.bundle()` and `.related()`.

    Deliberately thin: no behaviour lives here. build_context and
    related_candidates remain the actual implementation and stay directly
    testable (tests/test_context.py calls them straight, not through this
    class) — this only exists to be the stable thing a caller imports.
    """

    def __init__(self, store: TaskStore, memory: MemoryStore | None = None):
        self._store = store
        self._memory = memory

    def bundle(self, task_id: str, verbosity: str = "full") -> dict:
        return build_context(self._store, task_id, verbosity, memory=self._memory)

    def related(self, task: Task) -> list[dict]:
        return related_candidates(self._store, task)


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
    bundle: dict = dict(_BUNDLE_SKELETON)
    bundle.update({
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
    })
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

    RELEVANCE FLOOR (task:1f1e48e2): store.search() ranks but never excludes,
    so without a floor this always filled to MAX_RELATED with the best
    available match, however weak — see _passes_floor. Over-fetches
    MAX_RELATED*4+1 hits, the same multiplier store.search() itself uses
    internally, so filtering out the ones that fail the floor still leaves
    room to fill the cap from genuine matches instead of running dry early.
    """
    query = _query_terms(task)
    if not query:
        return []
    # store.search() builds its own OR-of-terms query internally — passing
    # plain words (not pre-formatted FTS5 syntax) is what makes term overlap
    # work instead of requiring an exact phrase.
    hits = store.search(query, limit=MAX_RELATED * 4 + 1)
    candidates = [t for t in hits if t.id != task.id]
    passing = [t for t in candidates if _passes_floor(query, _query_terms(t))]
    return [_task_summary(t) for t in passing[:MAX_RELATED]]


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

    RELEVANCE FLOOR (task:1f1e48e2): the same _passes_floor used by
    related_candidates, applied here too rather than reimplemented — one
    definition of the floor per the same reasoning _query_terms already
    gives for the query shape. MAX_LESSONS was originally set below
    MAX_RELATED as a stand-in brake on relevance; task:60ebca8e's
    introspection graded that ineffective, because a cap bounds volume and
    only a floor bounds quality — MAX_LESSONS is a volume limit now that the
    floor exists to do the job it was never able to do. Over-fetches
    MAX_LESSONS*4, mirroring recall()'s own internal over-fetch multiplier,
    so filtering still leaves room to fill the cap.
    """
    query = _query_terms(task)
    if not query:
        return []
    store_ = memory if memory is not None else MemoryStore(conn=store.conn)
    candidates = store_.recall(query, limit=MAX_LESSONS * 4, count_hit=False)
    passing = [m for m in candidates if _passes_floor(query, f"{m['slug']} {m['text']}")]
    return passing[:MAX_LESSONS]


def _size(bundle: dict) -> int:
    import json

    return len(json.dumps(bundle, default=str))


def _enforce_budget(bundle: dict) -> list[str]:
    """Drop whole sections, least useful first, until the bundle fits.

    Whole sections rather than partial truncation: a half-listed set of commits
    reads as complete and is worse than an absent one that says so. Grooming is
    the deliberate exception — see _trim_grooming and the module docstring's
    "GROOMING IS THE ONE EXCEPTION" section.
    """
    dropped: list[str] = []
    for section in TRIM_ORDER:
        if _size(bundle) <= CHAR_BUDGET:
            break
        if not bundle.get(section):
            continue
        if section == "grooming":
            grooming_dropped = _trim_grooming(bundle)
            if grooming_dropped:
                bundle["grooming_truncated"] = grooming_dropped
            if not bundle["grooming"]:
                bundle.pop("grooming_truncated", None)
                dropped.append("grooming")
            continue
        bundle[section] = [] if isinstance(bundle[section], list) else {}
        dropped.append(section)
        if section == "graph":
            bundle.pop("edges_truncated", None)
    return dropped


def _trim_grooming(bundle: dict) -> list[str]:
    """Shrink bundle["grooming"] field-by-field until the bundle fits.

    Grooming is a dict, not a list, so it can't reuse list-slicing the way
    related/lessons/commits do. Dropping it whole either keeps it complete or
    discards it entirely — exactly the waste this exists to avoid: a bundle
    whose only over-budget section is grooming would come back with grooming
    gone and thousands of characters of budget unspent. This drops one field
    at a time, least useful first (GROOMING_TRIM_ORDER), and stops the moment
    the bundle fits — so a task that is only slightly over budget loses only
    its least valuable grooming field, not the whole section.

    Operates on a shallow copy of the grooming dict, never the original: the
    same dict object came straight from `task.grooming`, and mutating it in
    place would corrupt the task's stored grooming for a caller elsewhere
    holding the same Task instance.

    Iterates every field actually present in the grooming dict, not just the
    ones named in GROOMING_TRIM_ORDER — a field outside that list (future
    schema, direct API write, older data) is dropped first, ahead of any
    named field, since it has no recorded priority to honour. This guarantees
    the loop empties the dict by the time it runs out of fields, so a bundle
    that is still over budget after every field is gone reads as "no
    grooming" through the same `if not bundle["grooming"]` check the caller
    already uses for every other outcome — no separate whole-section fallback
    needed here or in the caller.
    """
    grooming = dict(bundle.get("grooming") or {})
    bundle["grooming"] = grooming  # same object grooming mutates below, so
                                    # _size(bundle) reflects each drop as it happens
    known = [f for f in GROOMING_TRIM_ORDER if f in grooming]
    unknown = [f for f in grooming if f not in GROOMING_TRIM_ORDER]
    dropped: list[str] = []
    for field in unknown + known:
        if _size(bundle) <= CHAR_BUDGET:
            break
        if grooming.get(field):
            grooming[field] = [] if isinstance(grooming[field], list) else None
            dropped.append(field)
    bundle["grooming"] = {k: v for k, v in grooming.items() if v}
    return dropped
