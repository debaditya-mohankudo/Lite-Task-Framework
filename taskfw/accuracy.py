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

import re
from collections import Counter
from typing import Any

from taskfw.log import get_logger
from taskfw.models import Task
from taskfw.store import TaskStore

log = get_logger(__name__)

#: Grades a PREDICTED risk can carry. `missed` is deliberately not here: it
#: describes a surprise nothing predicted, so it cannot be a grade ON a
#: prediction. It is counted from `missed_surprises` instead.
GRADES = ("materialized", "avoided", "wrong")

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


def _normalise(text: str) -> str:
    """Collapse a risk to a comparable key. Trailing punctuation is not signal."""
    return re.sub(r"\s+", " ", (text or "").strip().lower()).rstrip(".")


def _risks(task: Task) -> list[dict]:
    """The grooming risks of a task, tolerating a bare string for a risk.

    The documented shape is {"id": ..., "text": ..., "graded": ...}, but a
    plain string is a plausible thing to write and dropping it would make an
    ungraded risk invisible — which is the one outcome this module exists to
    prevent. `id` is optional: risks written before task:f24be6e4 have none,
    and are never rewritten to gain one — see `_recurrence_key`.
    """
    raw = (task.grooming or {}).get("risks") or []
    out = []
    for risk in raw:
        if isinstance(risk, str):
            out.append({"id": None, "text": risk, "graded": None})
        elif isinstance(risk, dict):
            out.append({
                "id": risk.get("id"),
                "text": risk.get("text", ""),
                "graded": risk.get("graded"),
            })
    return out


def _recurrence_key(risk: dict) -> tuple[str, str] | None:
    """The key `grooming_accuracy` groups a risk's recurrence by, and which kind it is.

    Grouping by `id` is exact — it survives a reworded risk. Falling back to
    normalised text is the only option for a risk written before ids existed
    (task:f24be6e4) and is kept indefinitely rather than migrated, since
    stored grooming is never rewritten to fit a newer shape. Returns None for
    a risk with neither an id nor any text to key on.
    """
    if risk.get("id"):
        return ("id", risk["id"])
    key = _normalise(risk.get("text", ""))
    return ("text", key) if key else None


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


def loop_debt(store: TaskStore, limit: int = 10) -> dict[str, int]:
    """A cheap window onto the same debt `grooming_accuracy` reports in full.

    task:07f9270c — surfaced from `tasks__set_active`, which is called far
    more often per session than anyone runs `tasks__grooming_accuracy`
    voluntarily, so this walks a smaller, more recent window (`limit=10`
    against the aggregate's default 25) rather than the full aggregate.
    Uses `_task_grading`, the same classification `grooming_accuracy` uses,
    so the two can never disagree about what counts as skipped or ungraded.
    """
    tasks = store.list(status=("done",), limit=limit)
    skipped = 0
    ungraded = 0
    for task in tasks:
        risks, task_ungraded, any_graded = _task_grading(task)
        if not risks:
            continue
        ungraded += task_ungraded
        if not any_graded:
            skipped += 1
    return {
        "tasks_examined": len(tasks),
        "skipped_introspection": skipped,
        "ungraded_risks": ungraded,
    }


def _reported_totals(task: Task) -> dict[str, int]:
    """Sum the self-reported tallies across a task's introspection reports."""
    totals: Counter[str] = Counter()
    for report in task.introspection or []:
        for grade, count in (report.get("grooming_accuracy") or {}).items():
            if isinstance(count, int) and grade in GRADES:
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


def grooming_accuracy(store: TaskStore, limit: int = 25) -> dict[str, Any]:
    """Aggregate grooming grades over the most recently updated finished tasks.

    Returns raw tallies, recurring risks, tasks whose introspection was skipped,
    self-report disagreements, and interpretive signals. Everything except
    `signals` is a count or a list of ids — a caller that distrusts the
    interpretation can ignore it and keep the evidence.
    """
    tasks = store.list(status=("done",), limit=limit)

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
    seen: dict[str, dict[str, Any]] = {}

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
            keyed = _recurrence_key(risk)
            if keyed:
                kind, key = keyed
                entry = seen.setdefault(
                    key, {"text": risk["text"], "tasks": [], "grades": [], "keyed_by": kind}
                )
                if task.id not in entry["tasks"]:
                    entry["tasks"].append(task.id)
                entry["grades"].append(grade)
            if grade is None or grade == "":
                continue
            if grade in GRADES:
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
        for e in sorted(seen.values(), key=lambda e: -len(e["tasks"]))
        if len(e["tasks"]) >= RECURRENCE
    ][:MAX_RECURRING]

    result = {
        "tasks_examined": len(tasks),
        "tasks_with_grooming": with_grooming,
        "risks": {
            "total": risks_seen,
            **{g: tallies.get(g, 0) for g in GRADES},
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
