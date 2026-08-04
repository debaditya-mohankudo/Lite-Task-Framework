"""Dispatcher tests — advisory nudges, tested independent of any MCP tool.

The property worth protecting: introspection_nudge never mistakes an absence
of lessons for an absence of memory, and never nudges twice about the same
gap once anything has been promoted. tool_called's own property: `post` runs
exactly on a clean, successful exit — never on a refusal, never on a raise.
"""
from __future__ import annotations

import pytest

from taskfw.dispatcher import (
    _DRIFT_REFLECTION_INTERVAL,
    _drift_reflection_call_counts,
    drift_reflection_nudge,
    finish_nudge,
    introspection_nudge,
    switched_active_nudge,
    tool_called,
)
from taskfw.memory import MemoryStore
from taskfw.models import Task
from taskfw.store import TaskStore


@pytest.fixture
def stores(tmp_path):
    tasks = TaskStore(tmp_path / "t.db")
    memories = MemoryStore(conn=tasks.conn)
    yield tasks, memories
    tasks.close()


def _task(tasks: TaskStore) -> str:
    t = Task(title="A task")
    tasks.save(t)
    return t.id


class TestIntrospectionNudge:
    def test_none_when_nothing_lesson_shaped_is_present(self, stores):
        tasks, _ = stores
        tid = _task(tasks)
        assert introspection_nudge({"missed_surprises": ["huh"]}, tid, tasks.conn) is None

    def test_fires_on_the_canonical_new_knowledge_field(self, stores):
        tasks, _ = stores
        tid = _task(tasks)
        nudge = introspection_nudge({"new_knowledge": ["a lesson"]}, tid, tasks.conn)
        assert nudge is not None and "1 lesson" in nudge

    def test_fires_on_the_legacy_surprises_lesson_field(self, stores):
        tasks, _ = stores
        tid = _task(tasks)
        report = {"surprises": [{"surprise": "x", "lesson": "a lesson"}, {"surprise": "y"}]}
        nudge = introspection_nudge(report, tid, tasks.conn)
        assert nudge is not None and "1 lesson" in nudge

    def test_counts_across_both_fields(self, stores):
        tasks, _ = stores
        tid = _task(tasks)
        report = {
            "new_knowledge": ["a"],
            "surprises": [{"surprise": "x", "lesson": "b"}],
        }
        nudge = introspection_nudge(report, tid, tasks.conn)
        assert "2 lesson" in nudge

    def test_none_once_the_task_has_cited_any_memory(self, stores):
        tasks, memories = stores
        tid = _task(tasks)
        memories.record("a-slug", "A lesson long enough to pass the minimum length check.", tid)
        nudge = introspection_nudge({"new_knowledge": ["another lesson"]}, tid, tasks.conn)
        assert nudge is None


class TestFinishNudge:
    def test_fires_when_introspection_is_empty(self, stores):
        tasks, _ = stores
        t = Task(title="A task")
        tasks.save(t)
        nudge = finish_nudge(t)
        assert nudge is not None and t.id in nudge

    def test_none_once_a_report_exists(self, stores):
        tasks, _ = stores
        t = Task(title="A task", introspection=[{"date": "2026-08-01"}])
        tasks.save(t)
        assert finish_nudge(t) is None


class TestDriftReflectionNudge:
    def test_none_without_an_active_task(self):
        assert drift_reflection_nudge("scope1", "", "") is None

    def test_none_before_interval_reached(self):
        _drift_reflection_call_counts.clear()
        result = None
        for _ in range(_DRIFT_REFLECTION_INTERVAL - 1):
            result = drift_reflection_nudge("scope1", "t1", "Some task")
        assert result is None

    def test_fires_once_interval_reached(self):
        _drift_reflection_call_counts.clear()
        result = None
        for _ in range(_DRIFT_REFLECTION_INTERVAL):
            result = drift_reflection_nudge("scope1", "t1", "Some task")
        assert result is not None
        assert "task:t1" in result

    def test_recurs_every_interval(self):
        _drift_reflection_call_counts.clear()
        results = [
            drift_reflection_nudge("scope1", "t1", "Some task")
            for _ in range(_DRIFT_REFLECTION_INTERVAL * 2)
        ]
        fired = [i for i, r in enumerate(results, start=1) if r is not None]
        assert fired == [_DRIFT_REFLECTION_INTERVAL, _DRIFT_REFLECTION_INTERVAL * 2]

    def test_counts_are_scoped_per_scope_and_task(self):
        _drift_reflection_call_counts.clear()
        for _ in range(_DRIFT_REFLECTION_INTERVAL - 1):
            drift_reflection_nudge("scope1", "t1", "Some task")
        # a different task should not inherit scope1/t1's near-complete count
        result = drift_reflection_nudge("scope1", "t2", "Other task")
        assert result is None

    def test_title_is_optional(self):
        _drift_reflection_call_counts.clear()
        result = None
        for _ in range(_DRIFT_REFLECTION_INTERVAL):
            result = drift_reflection_nudge("scope1", "t1", "")
        assert result is not None
        assert "task:t1" in result


class TestToolCalled:
    def test_post_runs_on_a_successful_result(self):
        calls = []
        with tool_called(post=calls.append) as call:
            call.result = {"ok": True, "id": "x"}
        assert calls == [{"ok": True, "id": "x"}]

    def test_post_does_not_run_on_a_refusal(self):
        calls = []
        with tool_called(post=calls.append) as call:
            call.result = {"error": "No task 'x'"}
        assert calls == []

    def test_post_does_not_run_when_the_block_raises(self):
        calls = []
        with pytest.raises(ValueError):
            with tool_called(post=calls.append) as call:
                call.result = {"ok": True}
                raise ValueError("boom")
        assert calls == []

    def test_post_mutation_is_visible_in_the_returned_dict(self):
        def add_marker(result):
            result["marker"] = True

        def call_it():
            with tool_called(post=add_marker) as call:
                call.result = {"ok": True}
                return call.result

        assert call_it() == {"ok": True, "marker": True}

    def test_pre_runs_on_entry(self):
        calls = []
        with tool_called(pre=lambda: calls.append("pre")):
            pass
        assert calls == ["pre"]

    def test_no_hooks_is_a_no_op(self):
        with tool_called() as call:
            call.result = {"ok": True}
        # no exception, nothing to assert beyond "this doesn't blow up"


class TestSwitchedActiveNudge:
    def test_none_when_there_was_no_previous_active_task(self):
        assert switched_active_nudge(None, "abc123") is None

    def test_none_when_re_setting_the_same_active_task(self):
        assert switched_active_nudge("abc123", "abc123") is None

    def test_fires_on_a_genuine_switch(self):
        nudge = switched_active_nudge("aaa111", "bbb222")
        assert nudge is not None
        assert "aaa111" in nudge and "bbb222" in nudge
