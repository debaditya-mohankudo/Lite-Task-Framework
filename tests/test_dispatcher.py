"""Dispatcher tests — advisory nudges, tested independent of any MCP tool.

The property worth protecting: introspection_nudge never mistakes an absence
of lessons for an absence of memory, and never nudges twice about the same
gap once anything has been promoted. tool_called's own property: `post` runs
exactly on a clean, successful exit — never on a refusal, never on a raise.
"""
from __future__ import annotations

import logging

import pytest

from taskfw.dispatcher import (
    combine,
    completed_items,
    drift_reflection_nudge,
    finish_nudge,
    finish_reminder_nudge,
    introspection_nudge,
    is_implemented,
    next_open_item,
    phase_label,
    stale_memory_nudge,
    task_phase,
    tool_called,
    ungroomed_progress_nudge,
)
from taskfw.memory import MemoryStore
from taskfw.models import ResolutionItem, Task
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


class TestFinishReminderNudge:
    def test_none_with_no_resolution_items(self, stores):
        tasks, _ = stores
        t = Task(title="A task")
        tasks.save(t)
        assert finish_reminder_nudge(t) is None

    def test_none_on_partial_completion(self, stores):
        tasks, _ = stores
        t = Task(title="A task", resolution=[ResolutionItem("a", done=True), ResolutionItem("b")])
        tasks.save(t)
        assert finish_reminder_nudge(t) is None

    def test_fires_when_all_items_done_and_still_open(self, stores):
        tasks, _ = stores
        t = Task(title="A task", resolution=[ResolutionItem("a", done=True), ResolutionItem("b", done=True)])
        tasks.save(t)
        nudge = finish_reminder_nudge(t)
        assert nudge is not None and t.id in nudge

    def test_none_once_the_task_is_done(self, stores):
        tasks, _ = stores
        t = Task(
            title="A task",
            resolution=[ResolutionItem("a", done=True)],
            status="done",
        )
        tasks.save(t)
        assert finish_reminder_nudge(t) is None


class TestUngroomedProgressNudge:
    def test_none_with_no_progress(self, stores):
        tasks, _ = stores
        t = Task(title="A task", resolution=[ResolutionItem("a")])
        tasks.save(t)
        assert ungroomed_progress_nudge(t) is None

    def test_none_once_grooming_is_recorded(self, stores):
        tasks, _ = stores
        t = Task(
            title="A task",
            resolution=[ResolutionItem("a", done=True)],
            grooming={"clarifications": ["x"]},
        )
        tasks.save(t)
        assert ungroomed_progress_nudge(t) is None

    def test_fires_on_progress_with_no_grooming(self, stores):
        tasks, _ = stores
        t = Task(title="A task", resolution=[ResolutionItem("a", done=True), ResolutionItem("b")])
        tasks.save(t)
        nudge = ungroomed_progress_nudge(t)
        assert nudge is not None and t.id in nudge

    def test_refires_on_repeated_checks_while_still_ungroomed(self, stores):
        tasks, _ = stores
        t = Task(title="A task", resolution=[ResolutionItem("a", done=True)])
        tasks.save(t)
        assert ungroomed_progress_nudge(t) is not None
        assert ungroomed_progress_nudge(t) is not None


class TestStaleMemoryNudge:
    def test_none_on_unverified_standing(self):
        assert stale_memory_nudge({"slug": "x", "standing": "unverified"}) is None

    def test_none_on_confirmed_standing(self):
        assert stale_memory_nudge({"slug": "x", "standing": "confirmed"}) is None

    def test_none_on_superseded_standing(self):
        """Already flagged by definition — re-nudging would be noise."""
        assert stale_memory_nudge({"slug": "x", "standing": "superseded"}) is None

    def test_fires_on_contradicted_standing(self):
        nudge = stale_memory_nudge({"slug": "x", "standing": "contradicted"})
        assert nudge is not None and "x" in nudge

    def test_fires_on_disputed_standing(self):
        nudge = stale_memory_nudge({"slug": "x", "standing": "disputed"})
        assert nudge is not None and "x" in nudge

    def test_refires_on_repeated_checks_while_still_complete_and_open(self, stores):
        tasks, _ = stores
        t = Task(title="A task", resolution=[ResolutionItem("a", done=True)])
        tasks.save(t)
        assert finish_reminder_nudge(t) is not None
        assert finish_reminder_nudge(t) is not None


class TestIsImplemented:
    def test_false_with_no_resolution_items(self, stores):
        tasks, _ = stores
        t = Task(title="A task")
        tasks.save(t)
        assert is_implemented(t) is False

    def test_false_on_partial_completion(self, stores):
        tasks, _ = stores
        t = Task(title="A task", resolution=[ResolutionItem("a", done=True), ResolutionItem("b")])
        tasks.save(t)
        assert is_implemented(t) is False

    def test_true_when_all_items_done_regardless_of_status(self, stores):
        """Unlike finish_reminder_nudge, is_implemented does not gate on status —
        a done task with a complete checklist is still implemented."""
        tasks, _ = stores
        t = Task(title="A task", resolution=[ResolutionItem("a", done=True)], status="done")
        tasks.save(t)
        assert is_implemented(t) is True


class TestTaskPhase:
    def test_all_false_on_a_fresh_task(self, stores):
        tasks, _ = stores
        t = Task(title="A task")
        tasks.save(t)
        assert task_phase(t) == {"groomed": False, "implemented": False, "introspected": False}

    def test_groomed_true_when_grooming_is_non_empty(self, stores):
        tasks, _ = stores
        t = Task(title="A task", grooming={"risks": [{"text": "x", "graded": None}]})
        tasks.save(t)
        assert task_phase(t)["groomed"] is True

    def test_implemented_true_when_checklist_complete(self, stores):
        tasks, _ = stores
        t = Task(title="A task", resolution=[ResolutionItem("a", done=True)])
        tasks.save(t)
        assert task_phase(t)["implemented"] is True

    def test_introspected_true_when_a_report_exists(self, stores):
        tasks, _ = stores
        t = Task(title="A task", introspection=[{"date": "2026-08-04"}])
        tasks.save(t)
        assert task_phase(t)["introspected"] is True

    def test_all_three_independent_and_true_together(self, stores):
        tasks, _ = stores
        t = Task(
            title="A task",
            grooming={"risks": []},
            resolution=[ResolutionItem("a", done=True)],
            introspection=[{"date": "2026-08-04"}],
        )
        tasks.save(t)
        assert task_phase(t) == {"groomed": True, "implemented": True, "introspected": True}


class TestDriftReflectionNudge:
    """Stateless — this function still holds no counter itself (task:8be768df,
    task:1c8f0815). Without a call_count, it fires on every call, same as
    task:8be768df's simplification. With a call_count (sourced from
    claude-hooks' own per-session counter and passed through
    taskfw/drift_hook.py's stdin payload, since a fresh subprocess per call
    has nothing to count with), it throttles to every _DRIFT_NUDGE_INTERVAL-th
    call — the gating decision stays in taskfw even though the raw count
    comes from elsewhere.
    """

    def test_none_without_an_active_task(self):
        assert drift_reflection_nudge("", "") is None

    def test_fires_on_a_single_call(self):
        result = drift_reflection_nudge("t1", "Some task")
        assert result is not None
        assert "task:t1" in result

    def test_fires_every_call_without_a_counter(self):
        results = [drift_reflection_nudge("t1", "Some task") for _ in range(5)]
        assert all(r is not None for r in results)

    def test_throttles_to_every_eighth_call_with_a_counter(self):
        results = [drift_reflection_nudge("t1", "Some task", call_count=n) for n in range(1, 17)]
        assert [r is not None for r in results] == [
            False, False, False, False, False, False, False, True,
            False, False, False, False, False, False, False, True,
        ]

    def test_title_is_optional(self):
        result = drift_reflection_nudge("t1", "")
        assert result is not None
        assert "task:t1" in result

    def test_includes_phase_when_given(self):
        result = drift_reflection_nudge("t1", "Some task", "implementation")
        assert result is not None
        assert "[implementation]" in result

    def test_phase_is_optional(self):
        result = drift_reflection_nudge("t1", "Some task")
        assert result is not None
        assert "[" not in result

    def test_includes_next_item_when_given(self):
        result = drift_reflection_nudge("t1", "Some task", "implementation", "Write the thing")
        assert result is not None
        assert 'next: "Write the thing"' in result

    def test_next_item_is_optional(self):
        result = drift_reflection_nudge("t1", "Some task")
        assert result is not None
        assert "next:" not in result

    def test_includes_completed_items_when_given(self):
        result = drift_reflection_nudge(
            "t1", "Some task", "implementation", "Write the thing",
            active_task_completed_items=["Groom it", "Design it"],
        )
        assert result is not None
        assert "done: Groom it; Design it" in result

    def test_completed_items_is_optional(self):
        result = drift_reflection_nudge("t1", "Some task")
        assert result is not None
        assert "done:" not in result


class TestCompletedItems:
    def test_empty_when_no_resolution(self):
        task = Task(id="t1", type="task", title="x")
        assert completed_items(task) == []

    def test_only_done_items_in_order(self):
        task = Task(
            id="t1", type="task", title="x",
            resolution=[
                ResolutionItem(text="a", done=True),
                ResolutionItem(text="b", done=False),
                ResolutionItem(text="c", done=True),
            ],
        )
        assert completed_items(task) == ["a", "c"]


class TestNextOpenItem:
    def test_none_when_no_resolution(self):
        task = Task(id="t1", type="task", title="x")
        assert next_open_item(task) is None

    def test_none_when_all_done(self):
        task = Task(id="t1", type="task", title="x", resolution=[
            ResolutionItem(text="a", done=True),
            ResolutionItem(text="b", done=True),
        ])
        assert next_open_item(task) is None

    def test_returns_first_undone_item(self):
        task = Task(id="t1", type="task", title="x", resolution=[
            ResolutionItem(text="a", done=True),
            ResolutionItem(text="b", done=False),
            ResolutionItem(text="c", done=False),
        ])
        assert next_open_item(task) == "b"


class TestPhaseLabel:
    def test_ungroomed_is_grooming(self):
        assert phase_label({"groomed": False, "implemented": False, "introspected": False}) == "grooming"

    def test_groomed_not_implemented_is_implementation(self):
        assert phase_label({"groomed": True, "implemented": False, "introspected": False}) == "implementation"

    def test_implemented_not_introspected_is_introspection(self):
        assert phase_label({"groomed": True, "implemented": True, "introspected": False}) == "introspection"

    def test_all_true_is_done(self):
        assert phase_label({"groomed": True, "implemented": True, "introspected": True}) == "done"


class TestToolCalled:
    def test_post_runs_on_a_successful_result(self):
        calls = []
        with tool_called("tasks__probe", post=calls.append) as call:
            call.result = {"ok": True, "id": "x"}
        assert calls == [{"ok": True, "id": "x"}]

    def test_post_does_not_run_on_a_refusal(self):
        calls = []
        with tool_called("tasks__probe", post=calls.append) as call:
            call.result = {"error": "No task 'x'"}
        assert calls == []

    def test_post_does_not_run_when_the_block_raises(self):
        calls = []
        with pytest.raises(ValueError):
            with tool_called("tasks__probe", post=calls.append) as call:
                call.result = {"ok": True}
                raise ValueError("boom")
        assert calls == []

    def test_post_mutation_is_visible_in_the_returned_dict(self):
        def add_marker(result):
            result["marker"] = True

        def call_it():
            with tool_called("tasks__probe", post=add_marker) as call:
                call.result = {"ok": True}
                return call.result

        assert call_it() == {"ok": True, "marker": True}

    def test_pre_runs_on_entry(self):
        calls = []
        with tool_called("tasks__probe", pre=lambda: calls.append("pre")):
            pass
        assert calls == ["pre"]

    def test_no_hooks_is_a_no_op(self):
        with tool_called("tasks__probe") as call:
            call.result = {"ok": True}
        # no exception, nothing to assert beyond "this doesn't blow up"

    def test_logs_ok_on_success(self, caplog):
        with caplog.at_level(logging.INFO, logger="taskfw"):
            with tool_called("tasks__probe") as call:
                call.result = {"ok": True}
        assert "tool=tasks__probe OK" in caplog.text

    def test_logs_refuse_with_rule_on_an_error_result(self, caplog):
        with caplog.at_level(logging.INFO, logger="taskfw"):
            with tool_called("tasks__probe") as call:
                call.result = {"error": "No task 'x'", "rule": "exists"}
        assert "tool=tasks__probe REFUSE rule=exists" in caplog.text

    def test_logs_error_and_reraises_on_an_exception(self, caplog):
        with caplog.at_level(logging.INFO, logger="taskfw"):
            with pytest.raises(ValueError):
                with tool_called("tasks__probe") as call:
                    call.result = {"ok": True}
                    raise ValueError("boom")
        assert "tool=tasks__probe ERROR ValueError: boom" in caplog.text

    def test_logs_ok_on_a_non_dict_result(self, caplog):
        """tasks__list returns a bare list — the ok/error convention doesn't
        apply, and that must not be mistaken for a refusal."""
        with caplog.at_level(logging.INFO, logger="taskfw"):
            with tool_called("tasks__probe") as call:
                call.result = [{"id": "a"}, {"id": "b"}]
        assert "tool=tasks__probe OK" in caplog.text

    def test_logging_is_unconditional_even_with_no_hook_and_no_ok_key(self, caplog):
        """A read tool's raw dict has no 'ok' key at all — logging must not
        be gated on the same truthy-ok condition post() is gated on."""
        with caplog.at_level(logging.INFO, logger="taskfw"):
            with tool_called("tasks__probe") as call:
                call.result = {"id": "x", "title": "A task"}
        assert "tool=tasks__probe OK" in caplog.text

    def test_post_fires_on_a_success_result_with_no_ok_key(self):
        """A read tool (tasks__get, tasks__context) returns the raw object on
        success — no 'ok' key at all. post must still fire: gating is on the
        absence of 'error', not on a truthy 'ok', so hooks compose uniformly
        onto reads and writes alike (task:58782207)."""
        calls = []
        with tool_called("tasks__probe", post=calls.append) as call:
            call.result = {"id": "x", "title": "A task"}
        assert calls == [{"id": "x", "title": "A task"}]

    def test_post_does_not_run_on_a_non_dict_result(self):
        """tasks__list returns a bare list — never a refusal, but there's
        nowhere for apply_nudge to write a key, so post is skipped."""
        calls = []
        with tool_called("tasks__probe", post=calls.append) as call:
            call.result = [{"id": "a"}, {"id": "b"}]
        assert calls == []


class TestCombine:
    def test_runs_every_hook_in_order(self):
        calls = []
        hook = combine(lambda r: calls.append("a"), lambda r: calls.append("b"))
        hook({"ok": True})
        assert calls == ["a", "b"]

    def test_each_hook_sees_the_same_mutated_result(self):
        def add_x(result):
            result["x"] = True

        def add_y(result):
            result["y"] = result.get("x", False)

        result = {"ok": True}
        combine(add_x, add_y)(result)
        assert result == {"ok": True, "x": True, "y": True}

    def test_a_single_hook_composes_the_same_as_calling_it_directly(self):
        calls = []
        combine(calls.append)({"ok": True})
        assert calls == [{"ok": True}]
