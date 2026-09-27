"""The typed grooming shape — task:0b9de163 (review 2026-09-27 G2).

Two halves: the write side refuses an off-schema key at the MCP boundary, and
the read side folds any off-schema key a pre-type row carried into `other`, so
a round-trip of what a caller read back never trips the write side.
"""
from __future__ import annotations

import json

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import TypeAdapter

from taskfw import mcp_server as m
from taskfw.context import GROOMING_TRIM_ORDER
from taskfw.grooming import GROOMING_FIELDS, Grooming, normalise
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


def _legacy_row(store: TaskStore, grooming: dict) -> str:
    """A task whose stored JSON predates the type — written raw, as old rows were."""
    t = store.save(Task(title="legacy"))
    data = json.loads(store.conn.execute("SELECT data FROM tasks WHERE id=?", (t.id,)).fetchone()[0])
    data["grooming"] = grooming
    store.conn.execute("UPDATE tasks SET data=? WHERE id=?", (json.dumps(data), t.id))
    store.conn.commit()
    return t.id


class TestNormalise:
    def test_schema_keys_pass_through_unchanged(self):
        g = {"clarifications": ["a"], "risks": [{"id": "x", "text": "r", "graded": None}]}
        assert normalise(g) == g

    def test_off_schema_keys_fold_into_other_in_order(self):
        g = normalise({"risks": [], "summary": "why", "last_run_at": "2026-07-28", "n": 3})
        assert g == {"risks": [], "other": ["summary: why", "last_run_at: 2026-07-28", "n: 3"]}

    def test_fold_appends_to_an_existing_other(self):
        assert normalise({"other": ["kept"], "note": "x"})["other"] == ["kept", "note: x"]

    def test_empty_stays_empty(self):
        assert normalise(None) == {} and normalise({}) == {}

    def test_trim_order_covers_exactly_the_schema(self):
        # _trim_grooming relies on this to empty the dict — no unknown-key branch.
        assert sorted(GROOMING_TRIM_ORDER) == sorted(GROOMING_FIELDS)


class TestReadSide:
    def test_a_legacy_row_loads_with_its_extra_keys_folded(self, store):
        tid = _legacy_row(store, {"clarifications": ["c"], "summary": "kept prose"})
        assert store.get(tid).grooming == {"clarifications": ["c"], "other": ["summary: kept prose"]}

    def test_the_context_bundle_carries_only_schema_keys(self, store):
        tid = _legacy_row(store, {"prior_art": ["p"], "prior_art_note": "n", "last_run_at": "t"})
        assert set(m.tasks__context(tid)["grooming"]) <= set(GROOMING_FIELDS)


class TestWriteSide:
    @pytest.mark.anyio
    async def test_an_unknown_grooming_key_is_refused(self, store):
        t = m.tasks__create(title="t")
        with pytest.raises(ToolError, match="Extra inputs are not permitted"):
            await m.mcp.call_tool("tasks__update", {"task_id": t["id"], "grooming": {"sumary": "x"}})
        assert store.get(t["id"]).grooming == {}

    @pytest.mark.anyio
    async def test_an_unknown_key_on_a_risk_is_refused(self, store):
        t = m.tasks__create(title="t")
        with pytest.raises(ToolError, match="Extra inputs are not permitted"):
            await m.mcp.call_tool("tasks__update", {
                "task_id": t["id"], "grooming": {"risks": [{"text": "r", "grade": "wrong"}]},
            })

    def test_grading_a_legacy_task_round_trips(self, store):
        """The risk recorded in grooming: introspection re-passes the whole blob it
        read. That blob came through normalise, so the strict write accepts it.
        Validated against the same TypeAdapter the MCP layer builds, then called
        directly — call_tool runs the tool on a worker thread, where the test
        store's SQLite connection cannot follow."""
        tid = _legacy_row(store, {
            "risks": [{"id": "r1", "text": "might break", "graded": None}],
            "last_run_at": "2026-07-28",
        })
        g = m.tasks__context(tid)["grooming"]
        g["risks"][0]["graded"] = "avoided"
        TypeAdapter(Grooming).validate_python(g)
        m.tasks__update(tid, grooming=g)
        saved = store.get(tid).grooming
        assert saved["risks"][0]["graded"] == "avoided"
        assert saved["other"] == ["last_run_at: 2026-07-28"]
