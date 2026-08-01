"""Dispatcher tests — advisory nudges, tested independent of any MCP tool.

The property worth protecting: introspection_nudge never mistakes an absence
of lessons for an absence of memory, and never nudges twice about the same
gap once anything has been promoted.
"""
from __future__ import annotations

import pytest

from taskfw.dispatcher import introspection_nudge
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
