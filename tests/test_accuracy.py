"""Grooming-accuracy tests — the aggregate half of the feedback edge.

These pin the two things that make the tool worth having rather than merely
present: tallies are DERIVED from per-risk grades (never read back from a
self-reported count), and a finished task that graded nothing is reported as a
skipped introspection rather than counted as zero risks.

Both are silent failures. A tool that read the self-reported tally would agree
with itself forever, and a skipped introspection looks exactly like a task that
had no risks — which is precisely why they are asserted here.
"""
from __future__ import annotations

import pytest

from taskfw.accuracy import (
    MIN_SAMPLE,
    RECURRENCE,
    grooming_accuracy,
)
from taskfw.models import Task
from taskfw.store import TaskStore


@pytest.fixture
def store(tmp_path):
    s = TaskStore(tmp_path / "t.db")
    yield s
    s.close()


def finished(store, title: str, risks: list, introspection: list | None = None) -> Task:
    """A done task carrying graded risks. Saved open, then moved to done.

    Two saves because the state machine refuses a direct create-as-done, and
    going through the real transition is the point — a fixture that wrote the
    row directly would not notice if that rule changed.
    """
    task = store.save(Task(title=title, grooming={"risks": risks},
                           introspection=introspection or []))
    task.status = "done"
    return store.save(task)


class TestEmpty:
    def test_no_tasks_reports_zeroes_not_an_error(self, store):
        r = grooming_accuracy(store)
        assert r["tasks_examined"] == 0
        assert r["risks"]["total"] == 0
        assert r["predictive_value"] is None
        assert r["signals"] == []

    def test_task_without_grooming_is_examined_but_not_counted(self, store):
        finished(store, "no grooming", [])
        r = grooming_accuracy(store)
        assert r["tasks_examined"] == 1
        assert r["tasks_with_grooming"] == 0
        assert r["skipped_introspection"] == []


class TestScope:
    def test_open_tasks_are_excluded(self, store):
        store.save(Task(title="still going",
                        grooming={"risks": [{"text": "a", "graded": None}]}))
        r = grooming_accuracy(store)
        assert r["tasks_examined"] == 0

    def test_an_open_ungraded_task_is_not_a_skipped_introspection(self, store):
        """The scoping rule, stated as a test.

        An open task with ungraded risks is work in progress. Counting it as a
        lapse would make the skipped-introspection signal fire constantly and
        therefore mean nothing.
        """
        store.save(Task(title="wip", grooming={"risks": [{"text": "a", "graded": None}]}))
        assert grooming_accuracy(store)["skipped_introspection"] == []


class TestTallies:
    def test_grades_are_counted_by_kind(self, store):
        finished(store, "t", [
            {"text": "a", "graded": "materialized"},
            {"text": "b", "graded": "avoided"},
            {"text": "c", "graded": "wrong"},
            {"text": "d", "graded": None},
        ])
        r = grooming_accuracy(store)["risks"]
        assert (r["materialized"], r["avoided"], r["wrong"], r["ungraded"]) == (1, 1, 1, 1)
        assert r["total"] == 4

    def test_predictive_value_counts_materialized_and_avoided_as_useful(self, store):
        finished(store, "t", [
            {"text": "a", "graded": "materialized"},
            {"text": "b", "graded": "avoided"},
            {"text": "c", "graded": "wrong"},
            {"text": "d", "graded": "wrong"},
        ])
        assert grooming_accuracy(store)["predictive_value"] == 0.5

    def test_ungraded_risks_do_not_dilute_predictive_value(self, store):
        """Ungraded is not the same as wrong.

        Folding ungraded into the denominator would punish grooming for
        introspection's omission and quietly reward skipping the grading.
        """
        finished(store, "t", [
            {"text": "a", "graded": "materialized"},
            {"text": "b", "graded": None},
            {"text": "c", "graded": None},
        ])
        assert grooming_accuracy(store)["predictive_value"] == 1.0

    def test_unrecognised_grade_is_reported_not_dropped(self, store):
        finished(store, "t", [{"text": "a", "graded": "sort of"}])
        r = grooming_accuracy(store)["risks"]
        assert r["unrecognised"] == {"sort of": 1}
        assert r["total"] == 1

    def test_a_bare_string_risk_counts_as_ungraded(self, store):
        finished(store, "t", ["just a sentence"])
        assert grooming_accuracy(store)["risks"]["ungraded"] == 1


class TestSkippedIntrospection:
    def test_finished_with_risks_and_no_grades_is_flagged(self, store):
        task = finished(store, "t", [{"text": "a", "graded": None},
                                     {"text": "b", "graded": None}])
        r = grooming_accuracy(store)
        assert r["skipped_introspection"] == [task.id]
        assert any("skipped" in s for s in r["signals"])

    def test_partial_grading_is_not_a_skip(self, store):
        finished(store, "t", [{"text": "a", "graded": "wrong"},
                              {"text": "b", "graded": None}])
        assert grooming_accuracy(store)["skipped_introspection"] == []

    def test_one_skip_signals_without_needing_a_sample_size(self, store):
        """No threshold guards this one, deliberately.

        A ratio needs a sample before it means anything. A skipped
        introspection does not — one is already the loop not running.
        """
        finished(store, "t", [{"text": "a", "graded": None}])
        assert grooming_accuracy(store)["signals"]


class TestDerivedNotReported:
    def test_tallies_come_from_grades_not_the_self_reported_count(self, store):
        """The load-bearing assertion.

        The report claims ten materialized; the risks say one. Trusting the
        report would make the tool agree with whatever anyone typed.
        """
        finished(store, "t",
                 [{"text": "a", "graded": "materialized"}],
                 [{"grooming_accuracy": {"materialized": 10, "wrong": 4}}])
        assert grooming_accuracy(store)["risks"]["materialized"] == 1

    def test_disagreement_is_reported_rather_than_silently_resolved(self, store):
        task = finished(store, "t",
                        [{"text": "a", "graded": "materialized"}],
                        [{"grooming_accuracy": {"materialized": 10}}])
        d = grooming_accuracy(store)["self_report_disagreements"]
        assert len(d) == 1
        assert d[0]["task"] == task.id
        assert d[0]["reported"] == {"materialized": 10}
        assert d[0]["derived"] == {"materialized": 1}

    def test_agreement_produces_no_disagreement_entry(self, store):
        finished(store, "t",
                 [{"text": "a", "graded": "avoided"}],
                 [{"grooming_accuracy": {"avoided": 1}}])
        assert grooming_accuracy(store)["self_report_disagreements"] == []

    def test_missed_surprises_are_summed_across_reports(self, store):
        finished(store, "t",
                 [{"text": "a", "graded": "materialized"}],
                 [{"missed_surprises": ["one", "two"]}, {"missed_surprises": ["three"]}])
        assert grooming_accuracy(store)["missed_surprises"] == 3


class TestRecurring:
    def test_a_risk_in_two_tasks_recurs(self, store):
        a = finished(store, "one", [{"text": "Storage format unresolved", "graded": "wrong"}])
        b = finished(store, "two", [{"text": "storage format unresolved.", "graded": "wrong"}])
        recurring = grooming_accuracy(store)["recurring_risks"]
        assert len(recurring) == 1
        assert set(recurring[0]["tasks"]) == {a.id, b.id}

    def test_a_repeat_within_one_task_is_not_a_pattern(self, store):
        finished(store, "one", [{"text": "same", "graded": "wrong"},
                                {"text": "same", "graded": "wrong"}])
        assert grooming_accuracy(store)["recurring_risks"] == []

    def test_recurrence_threshold_is_distinct_tasks(self, store):
        for i in range(RECURRENCE):
            finished(store, f"t{i}", [{"text": "shared", "graded": "avoided"}])
        assert len(grooming_accuracy(store)["recurring_risks"]) == 1


class TestSignals:
    def test_below_the_sample_size_no_ratio_signal_fires(self, store):
        finished(store, "t", [{"text": "a", "graded": "wrong"}])
        assert grooming_accuracy(store)["signals"] == []

    def test_mostly_wrong_says_grooming_asks_the_wrong_questions(self, store):
        finished(store, "t", [{"text": f"r{i}", "graded": "wrong"}
                              for i in range(MIN_SAMPLE)])
        assert any("wrong questions" in s for s in grooming_accuracy(store)["signals"])

    def test_mostly_surprises_says_grooming_asks_too_few_questions(self, store):
        finished(store, "t",
                 [{"text": f"r{i}", "graded": "materialized"} for i in range(MIN_SAMPLE)],
                 [{"missed_surprises": [f"s{i}" for i in range(MIN_SAMPLE * 2)]}])
        assert any("not asking enough" in s for s in grooming_accuracy(store)["signals"])

    def test_accurate_grooming_emits_nothing(self, store):
        """Silence is the success case, and it has to be reachable."""
        finished(store, "t", [{"text": f"r{i}", "graded": "materialized"}
                              for i in range(MIN_SAMPLE)])
        assert grooming_accuracy(store)["signals"] == []


class TestLimit:
    def test_limit_bounds_the_tasks_examined(self, store):
        for i in range(5):
            finished(store, f"t{i}", [{"text": "a", "graded": "wrong"}])
        assert grooming_accuracy(store, limit=2)["tasks_examined"] == 2
