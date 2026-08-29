"""Grading the grader — grooming accuracy aggregated across finished tasks.

Introspection grades the risks a grooming pass predicted. That closes the loop
for ONE task. This closes it for the loop itself.

WHY IT EXISTS. The methodology says repeated `wrong` grades mean grooming is
asking the wrong questions and repeated `missed` grades mean it is not asking
enough of them, and that both are signals to change grooming rather than merely
note the miss. Nothing could see "repeated": grades live inside a task's own
grooming, so a pattern across tasks was writable and unreadable. That made the
framework's central claim — the feedback edge — true per task and inert in
aggregate.

DERIVED, NEVER READ BACK. An introspection report carries a self-reported
`grooming_accuracy` tally. It is NOT the source here: the per-risk `graded`
values are, and the tally is recomputed from them every time. The same reason
progress is derived from the checklist — a stored count can disagree with the
thing it counts, and a computed one cannot. A disagreement is reported rather
than silently resolved, because it means someone's summary drifted from their
own evidence and that is worth seeing.

FINISHED TASKS ONLY, and the scoping is load-bearing rather than incidental.
An open task with ungraded risks is a task in progress. A *finished* task with
ungraded risks is a skipped introspection — the failure the methodology calls
the one that actually happens, since the work is done and moving on feels like
progress. Only by looking at closed tasks does that become detectable at all.

NO THRESHOLD HIDES A ZERO. Every count is reported whatever it is; thresholds
only decide whether an interpretive signal is emitted. A caller can always see
the raw tallies and disagree with the interpretation.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from taskfw.log import get_logger
from taskfw.risk import coerce, normalise_text
from taskfw.task import Task
from taskfw.store import TaskStore

log = get_logger(__name__)

#: Grades a PREDICTED grooming risk can carry. `missed` is deliberately not
#: here: it describes a surprise nothing predicted, so it cannot be a grade ON
#: a prediction. It is counted from `missed_surprises` instead.
GROOMING_GRADES = ("materialized", "avoided", "wrong")

#: A prediction earns its keep if it came true or changed the plan. `wrong` is
#: the only grade that represents a pass spent on noise.
USEFUL = ("materialized", "avoided")

#: Below this many graded risks, any ratio is noise and no signal is emitted.
MIN_SAMPLE = 3

#: Share of graded risks graded `wrong` above which grooming is asking the
#: wrong questions.
WRONG_SHARE = 0.4

#: Share of all findings that were surprises above which grooming is not asking
#: enough questions.
MISSED_SHARE = 0.4

#: A risk text must appear in this many DISTINCT tasks to count as recurring.
#: Within one task a repeat is a duplicate; across tasks it is a pattern.
RECURRENCE = 2

MAX_RECURRING = 10


def _risks(task: Task) -> list[dict]:
    """The grooming risks of a task, projected to {"id", "text", "graded"}.

    Shape-coercion (bare string, dict, or worse -> dict) is `taskfw.risk.coerce`;
    this adds only the projection to the exact three keys this module compares
    on. A plain string is a plausible thing to write and dropping it would make
    an ungraded risk invisible — the one outcome this module exists to prevent.
    `id` is optional: risks written before task:f24be6e4 have none, and are
    never rewritten to gain one — see `_RecurrenceGrouper`. A non-str, non-dict
    entry is skipped, matching `coerce`'s callers that never hand it one.
    """
    raw = (task.grooming or {}).get("risks") or []
    out = []
    for risk in raw:
        if not isinstance(risk, (str, dict)):
            continue
        c = coerce(risk)
        out.append({"id": c.get("id"), "text": c.get("text", ""), "graded": c.get("graded")})
    return out


class _RecurrenceGrouper:
    """Groups risks across tasks into recurrence entries for `grooming_accuracy`.

    A risk written via `tasks__update` always gets a fresh, framework-assigned
    id (`_merge_grooming_risks`), so two different tasks predicting the same
    risk text end up with two different ids — grouping by id alone would key
    them apart and recurrence would never fire for anything groomed after
    task:f24be6e4. Text is the real recurrence signal; id only matters when a
    risk gets reworded but keeps its id (a regroom of the same task) or when
    an id is deliberately reused.

    Id-less risks (written before ids existed) group by normalised text only,
    in their own keyspace — they never merge with an id-bearing risk, since an
    id-bearing entry is a confirmed later prediction and matching it against a
    legacy text-only risk would associate them by coincidence rather than by
    the framework's own identity for either one.
    """

    def __init__(self) -> None:
        self._entries: list[dict[str, Any]] = []
        self._idless_text_index: dict[str, int] = {}
        self._id_index: dict[str, int] = {}
        self._id_text_index: dict[str, int] = {}

    def add(self, risk: dict, task_id: str, grade: Any) -> None:
        text = risk.get("text", "")
        key = normalise_text(text)
        rid = risk.get("id")

        if rid:
            idx = self._id_index.get(rid)
            matched_via = "id" if idx is not None else None
            if idx is None and key:
                idx = self._id_text_index.get(key)
                matched_via = "text" if idx is not None else None
            if idx is None:
                idx = len(self._entries)
                self._entries.append({"text": text, "tasks": [], "grades": [], "keyed_by": "id"})
            elif matched_via == "text":
                self._entries[idx]["keyed_by"] = "text"
            self._id_index[rid] = idx
            if key:
                self._id_text_index[key] = idx
        else:
            if not key:
                return
            idx = self._idless_text_index.get(key)
            if idx is None:
                idx = len(self._entries)
                self._entries.append({"text": text, "tasks": [], "grades": [], "keyed_by": "text"})
            self._idless_text_index[key] = idx

        entry = self._entries[idx]
        if task_id not in entry["tasks"]:
            entry["tasks"].append(task_id)
        entry["grades"].append(grade)

    def entries(self) -> list[dict[str, Any]]:
        return self._entries


def _task_grading(task: Task) -> tuple[list[dict], int, bool]:
    """A task's risks, how many are ungraded, and whether any are graded.

    The single classification both `grooming_accuracy` (which finished tasks
    skipped introspection entirely) and `loop_debt` (task:07f9270c, read from
    `tasks__set_active`) key off, so the two can never disagree about what
    counts as graded.
    """
    risks = _risks(task)
    ungraded = sum(1 for r in risks if not r["graded"])
    any_graded = any(r["graded"] for r in risks)
    return risks, ungraded, any_graded


def _debt_counts(tasks: list[Task]) -> tuple[int, int]:
    """(tasks that graded nothing, total ungraded risks) over `tasks`.

    Extracted so the scoped window and the unscoped remainder are counted by
    literally the same code — a "what you are not being shown" figure computed
    by a second, slightly different rule would be worse than not showing it.
    """
    skipped = 0
    ungraded = 0
    for task in tasks:
        risks, task_ungraded, any_graded = _task_grading(task)
        if not risks:
            continue
        ungraded += task_ungraded
        if not any_graded:
            skipped += 1
    return skipped, ungraded


def loop_debt(store: TaskStore, limit: int = 10,
              scope: str | None = None) -> dict[str, Any]:
    """A cheap window onto the same debt `grooming_accuracy` reports in full.

    task:07f9270c — surfaced from `tasks__set_active`, which is called far
    more often per session than anyone runs `tasks__grooming_accuracy`
    voluntarily, so this walks a smaller, more recent window (`limit=10`
    against the aggregate's default 25) rather than the full aggregate.
    Uses `_task_grading`, the same classification `grooming_accuracy` uses,
    so the two can never disagree about what counts as skipped or ungraded.

    THE COUNT NOW SAYS WHAT IT COUNTED. concept:grooming-accuracy-aggregate
    recorded, as an accepted risk, that this number was computed over the
    whole shared store — so a debt figure shown while working in one repo
    could be driven entirely by unrelated tasks in another, and nothing in the
    output revealed that. `scope` narrows the window to one project, and
    `scope` is echoed in the result either way: a caller can always tell
    whether it is reading a project figure or a global one, which is the part
    that was actually missing.

    A scoped call additionally reports `unscoped_not_counted`. Every task
    written before `Task.scope` existed is unscoped and therefore outside any
    project window, so a scoped count is genuinely smaller than the truth.
    Reporting the remainder is what keeps that a narrowing rather than a
    disappearance — an omission must not look like an absence.
    """
    tasks = store.list(status=("done",), scope=scope, limit=limit)
    skipped, ungraded = _debt_counts(tasks)
    result: dict[str, Any] = {
        "tasks_examined": len(tasks),
        "skipped_introspection": skipped,
        "ungraded_risks": ungraded,
        "scope": scope or "global",
    }
    if scope:
        unscoped = store.list(status=("done",), scope="", limit=limit)
        un_skipped, un_ungraded = _debt_counts(unscoped)
        if un_skipped or un_ungraded:
            result["unscoped_not_counted"] = {
                "skipped_introspection": un_skipped,
                "ungraded_risks": un_ungraded,
            }
    return result


def _reported_totals(task: Task) -> dict[str, int]:
    """Sum the self-reported tallies across a task's introspection reports."""
    totals: Counter[str] = Counter()
    for report in task.introspection or []:
        for grade, count in (report.get("grooming_accuracy") or {}).items():
            if isinstance(count, int) and grade in GROOMING_GRADES:
                totals[grade] += count
    return dict(totals)


def _nonzero(counts: dict[str, int]) -> dict[str, int]:
    """Drop zero entries so two tallies compare on content, not on shape.

    A self-reported tally usually names every grade including the ones that did
    not occur (`{"avoided": 1, "materialized": 0, "wrong": 0}`), while a derived
    Counter only holds grades actually seen (`{"avoided": 1}`). Comparing those
    directly made every task that graded a single risk type look like a
    disagreement — an alarm this module raised against itself, in the one place
    it claims a derived count cannot disagree with what it counts.
    """
    return {grade: count for grade, count in counts.items() if count}


def _missed(task: Task) -> int:
    return sum(len(r.get("missed_surprises") or []) for r in task.introspection or [])


def grooming_accuracy(store: TaskStore, limit: int = 25,
                      scope: str | None = None) -> dict[str, Any]:
    """Aggregate grooming grades over the most recently updated finished tasks.

    Returns raw tallies, recurring risks, tasks whose introspection was skipped,
    self-report disagreements, and interpretive signals. Everything except
    `signals` is a count or a list of ids — a caller that distrusts the
    interpretation can ignore it and keep the evidence.

    `scope` narrows to one project and is echoed in the result; omitted, the
    aggregate stays global and says so. See `loop_debt` for why the echo is
    not optional. Note that a scoped aggregate excludes every task written
    before `Task.scope` existed, since those carry no scope to match.
    """
    tasks = store.list(status=("done",), scope=scope, limit=limit)

    tallies: Counter[str] = Counter()
    unrecognised: Counter[str] = Counter()
    ungraded = 0
    missed = 0
    with_grooming = 0
    #: Counted independently of the grade buckets. Deriving the total by adding
    #: them up meant an unrecognised grade fell through every bucket and left
    #: the risk uncounted — the exact failure this module claims to prevent.
    risks_seen = 0
    skipped: list[str] = []
    disagreements: list[dict] = []
    grouper = _RecurrenceGrouper()

    for task in tasks:
        risks, task_ungraded, any_graded = _task_grading(task)
        if not risks:
            continue
        with_grooming += 1
        missed += _missed(task)

        graded_here: Counter[str] = Counter()
        for risk in risks:
            risks_seen += 1
            grade = risk["graded"]
            grouper.add(risk, task.id, grade)
            if grade is None or grade == "":
                continue
            if grade in GROOMING_GRADES:
                tallies[grade] += 1
                graded_here[grade] += 1
            else:
                # Never dropped. An unrecognised grade that vanished would be an
                # omission indistinguishable from an absence.
                unrecognised[str(grade)] += 1

        ungraded += task_ungraded
        if not any_graded:
            # Finished, predicted risks, never graded any of them.
            skipped.append(task.id)

        reported = _reported_totals(task)
        derived = dict(graded_here)
        if _nonzero(reported) and _nonzero(reported) != _nonzero(derived):
            disagreements.append({
                "task": task.id, "reported": reported, "derived": derived,
            })

    graded_total = sum(tallies.values())
    recurring = [
        {"text": e["text"], "tasks": e["tasks"], "grades": e["grades"], "keyed_by": e["keyed_by"]}
        for e in sorted(grouper.entries(), key=lambda e: -len(e["tasks"]))
        if len(e["tasks"]) >= RECURRENCE
    ][:MAX_RECURRING]

    result = {
        "tasks_examined": len(tasks),
        "tasks_with_grooming": with_grooming,
        # Always present, whichever way it was called — see loop_debt. A
        # tally that does not say what it ranged over is a tally a reader
        # will assume is complete.
        "scope": scope or "global",
        "risks": {
            "total": risks_seen,
            **{g: tallies.get(g, 0) for g in GROOMING_GRADES},
            "ungraded": ungraded,
            "unrecognised": dict(unrecognised),
        },
        "missed_surprises": missed,
        "predictive_value": (
            round(sum(tallies.get(g, 0) for g in USEFUL) / graded_total, 2)
            if graded_total else None
        ),
        "recurring_risks": recurring,
        "skipped_introspection": skipped,
        "self_report_disagreements": disagreements,
        "signals": _signals(tallies, graded_total, missed, skipped),
    }
    log.info("grooming accuracy: tasks=%d graded=%d ungraded=%d missed=%d signals=%d",
             len(tasks), graded_total, ungraded, missed, len(result["signals"]))
    return result


def _signals(tallies: Counter, graded_total: int, missed: int, skipped: list[str]) -> list[str]:
    """Interpretations, each traceable to a stated threshold.

    Kept separate from the counts so the judgement is auditable and refusable.
    A signal is emitted only where the sample supports it — the exception is a
    skipped introspection, which needs no sample size because a single one is
    already the loop not running.
    """
    out: list[str] = []
    if skipped:
        out.append(
            f"{len(skipped)} finished task(s) predicted risks and graded none. "
            "Introspection was skipped — the predictions taught nothing."
        )
    if graded_total >= MIN_SAMPLE:
        wrong = tallies.get("wrong", 0) / graded_total
        if wrong >= WRONG_SHARE:
            out.append(
                f"{wrong:.0%} of graded risks were wrong. Grooming is asking the "
                "wrong questions — change what it asks, not just the estimates."
            )
        findings = graded_total + missed
        if findings and missed / findings >= MISSED_SHARE:
            out.append(
                f"{missed / findings:.0%} of findings were surprises nothing predicted. "
                "Grooming is not asking enough questions."
            )
    return out
