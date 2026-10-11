"""The typed introspection report — task:6cab3061.

A malformed report is refused before the write, an identical retry appends
nothing, and an empty report does not count as an introspection pass.
"""
from __future__ import annotations

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import TypeAdapter

from taskfw import mcp_server as m
from taskfw.dispatcher.phase import is_introspected
from taskfw.introspection import IntrospectionReport, has_content
from taskfw.store import TaskStore
from taskfw.task import Task


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    s = TaskStore(tmp_path / "t.db")
    m.set_store(s)
    monkeypatch.setenv("TASKFW_SCOPE", "/test/workspace")
    yield s
    s.close()
    m.set_store(None)


class TestSchema:
    @pytest.mark.anyio
    async def test_an_unknown_key_is_refused_before_the_write(self, store):
        t = m.tasks__create(title="t")
        with pytest.raises(ToolError, match="Extra inputs are not permitted"):
            await m.mcp.call_tool("tasks__add_introspection",
                                  {"task_id": t["id"], "report": {"sumary": "x"}})
        assert store.get(t["id"]).introspection == []

    def test_no_field_is_required(self):
        TypeAdapter(IntrospectionReport).validate_python({"other": ["free text"]})

    def test_the_canonical_and_legacy_live_shapes_validate(self):
        ta = TypeAdapter(IntrospectionReport)
        ta.validate_python({
            "date": "2026-10-11", "grooming_accuracy": {"avoided": 1, "wrong": 0},
            "missed_surprises": ["a", {"text": "b"}], "new_knowledge": ["k"],
            "stale_knowledge_flagged": [], "highest_leverage": "h", "overall_assessment": "o",
        })
        ta.validate_python({"grooming_accuracy": None, "surprises": [{"lesson": "l"}]})


class TestEmptyReport:
    def test_empty_report_is_refused_and_not_stored(self, store):
        t = m.tasks__create(title="t")
        assert "error" in m.tasks__add_introspection(t["id"], {})
        assert "error" in m.tasks__add_introspection(t["id"], {"new_knowledge": []})
        assert store.get(t["id"]).introspection == []

    def test_has_content(self):
        assert not has_content({}) and not has_content(None)
        assert has_content({"date": "2026-10-11"})

    def test_an_empty_stored_report_does_not_count_as_introspected(self):
        assert not is_introspected(Task(title="x", introspection=[{}]))
        assert is_introspected(Task(title="x", introspection=[{}, {"date": "d"}]))

    def test_an_empty_stored_report_does_not_silence_finish_nudge(self, store):
        t = store.save(Task(title="x", introspection=[{}]))
        assert "finish_nudge" in m.tasks__finish(t.id)


class TestIdempotentRetry:
    def test_identical_retry_appends_nothing(self, store):
        t = m.tasks__create(title="t")
        report = {"date": "2026-10-11", "new_knowledge": ["a lesson"]}
        first = m.tasks__add_introspection(t["id"], report)
        again = m.tasks__add_introspection(t["id"], dict(report))
        assert first["reports"] == 1 and "duplicate" not in first
        assert again["ok"] and again["duplicate"] is True and again["reports"] == 1
        assert "introspection_nudge" not in again
        assert len(store.get(t["id"]).introspection) == 1

    def test_a_different_report_still_appends(self, store):
        t = m.tasks__create(title="t")
        m.tasks__add_introspection(t["id"], {"date": "2026-10-11"})
        r = m.tasks__add_introspection(t["id"], {"date": "2026-10-12"})
        assert r["reports"] == 2 and "duplicate" not in r
