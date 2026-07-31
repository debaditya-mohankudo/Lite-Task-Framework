"""MCP tool tests — the portable surface.

Tools are exercised by calling them directly rather than over the wire —
MCPServer's decorator registers the function and returns it unchanged, so this
reaches the same code path a host does without standing up a transport.

The property worth protecting here is that tools enforce nothing themselves.
Every rule check goes through taskfw.lifecycle, so a tool cannot enforce a
different set than a hook does.
"""
from __future__ import annotations

import pytest

from taskfw import mcp_server as m
from taskfw.store import TaskStore


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    s = TaskStore(tmp_path / "t.db")
    m.set_store(s)
    monkeypatch.setenv("TASKFW_SCOPE", "/test/workspace")
    yield s
    s.close()
    m.set_store(None)


def create(**kw):
    kw.setdefault("title", "A task")
    return m.tasks__create(**kw)


class TestCreate:
    def test_creates_a_task(self):
        r = create(title="Build it")
        assert r["ok"] and r["type"] == "task" and r["status"] == "open"

    def test_creates_an_epic(self):
        assert create(title="An epic", type="epic")["type"] == "epic"

    def test_rejects_an_unknown_type_via_lifecycle(self):
        r = create(type="story")
        assert r["rule"] == "type" and "story" in r["error"]

    def test_rejects_an_epic_with_a_parent(self):
        e = create(title="e", type="epic")
        r = create(title="e2", type="epic", parent=e["id"])
        assert r["rule"] == "parent"

    def test_rejects_a_missing_parent(self):
        assert "error" in create(parent="nosuch")

    def test_resolution_is_a_plain_list_of_strings(self):
        r = create(resolution=["one", "two"])
        assert m.tasks__get(r["id"])["resolution"] == [
            {"text": "one", "done": False}, {"text": "two", "done": False}
        ]


class TestUpdate:
    def test_only_given_fields_change(self):
        t = create(title="original", motivation="keep me")
        m.tasks__update(t["id"], title="renamed")
        got = m.tasks__get(t["id"])
        assert got["title"] == "renamed" and got["motivation"] == "keep me"

    def test_illegal_transition_is_refused_by_lifecycle(self):
        t = create()
        m.tasks__update(t["id"], status="done")
        r = m.tasks__update(t["id"], status="open")
        assert r["rule"] == "transition"

    def test_legal_transition_is_allowed(self):
        t = create()
        assert m.tasks__update(t["id"], status="blocked")["status"] == "blocked"

    def test_unknown_task(self):
        assert "error" in m.tasks__update("nope", title="x")


class TestChecklist:
    def test_ticking_updates_progress(self):
        t = create(resolution=["a", "b"])
        r = m.tasks__check_item(t["id"], 0)
        assert r["progress"] == {"done": 1, "total": 2}

    def test_out_of_range_index_is_rejected(self):
        t = create(resolution=["a"])
        assert "error" in m.tasks__check_item(t["id"], 5)


class TestFinish:
    def test_finishes_an_open_task(self):
        t = create()
        assert m.tasks__finish(t["id"], reason="shipped")["status"] == "done"

    def test_finishing_an_already_done_task_is_idempotent(self):
        """Follows from the same-status rule, and is the friendlier behaviour.

        A retry or a duplicate call should not surface as an error when the
        task is already in the state the caller wants.
        """
        t = create()
        m.tasks__finish(t["id"])
        r = m.tasks__finish(t["id"])
        assert r["status"] == "done" and "error" not in r

    def test_refuses_to_finish_an_abandoned_task(self):
        """Abandoned is terminal and is NOT the state the caller asked for."""
        t = create()
        m.tasks__update(t["id"], status="abandoned")
        assert m.tasks__finish(t["id"])["rule"] == "transition"


class TestListAndSearch:
    def test_list_defaults_to_open_and_blocked(self):
        a = create(title="open one")
        b = create(title="done one")
        m.tasks__finish(b["id"])
        assert [t["title"] for t in m.tasks__list()] == ["open one"]
        assert len(m.tasks__list(status="")) == 2

    def test_search_finds_by_motivation(self):
        create(title="alpha", motivation="uses a widget")
        create(title="beta")
        assert [t["title"] for t in m.tasks__search("widget")] == ["alpha"]


class TestGraph:
    def test_link_unlink_round_trip(self):
        a, b = create(title="a"), create(title="b")
        assert m.tasks__link(a["id"], b["id"], "depends_on")["created"] is True
        assert m.tasks__edges(a["id"])["outgoing"][0]["to_id"] == b["id"]
        assert m.tasks__unlink(a["id"], b["id"])["removed"] == 1

    def test_link_rejects_unknown_tasks(self):
        a = create()
        assert "error" in m.tasks__link(a["id"], "nosuch")

    def test_commit_capture_is_idempotent(self):
        t = create()
        assert m.tasks__add_commit(t["id"], "abc")["recorded"] is True
        assert m.tasks__add_commit(t["id"], "abc")["recorded"] is False


class TestActiveTask:
    def test_set_get_clear(self):
        t = create()
        assert m.tasks__active()["active"] is None
        m.tasks__set_active(t["id"])
        assert m.tasks__active()["active"] == t["id"]
        m.tasks__clear_active()
        assert m.tasks__active()["active"] is None

    def test_set_active_rejects_unknown_task(self):
        assert "error" in m.tasks__set_active("nosuch")

    def test_context_falls_back_to_the_active_task(self):
        t = create(title="the active one")
        m.tasks__set_active(t["id"])
        assert m.tasks__context()["task"]["title"] == "the active one"

    def test_context_without_task_or_active_explains_itself(self):
        assert "error" in m.tasks__context()


class TestDecisions:
    def test_decision_surfaces_in_context(self):
        t = create()
        m.tasks__add_decision(t["id"], "chose the library route")
        ctx = m.tasks__context(t["id"])
        assert ctx["decisions"][0]["text"] == "chose the library route"

    def test_decision_on_unknown_task(self):
        assert "error" in m.tasks__add_decision("nosuch", "x")


class TestRegistration:
    @pytest.mark.anyio
    async def test_every_tool_is_registered_with_the_server(self):
        names = {t.name for t in await m.mcp.list_tools()}
        assert {
            "tasks__context", "tasks__get", "tasks__list", "tasks__search",
            "tasks__create", "tasks__update", "tasks__check_item", "tasks__finish",
            "tasks__add_decision", "tasks__link", "tasks__unlink", "tasks__edges",
            "tasks__add_commit", "tasks__set_active", "tasks__active", "tasks__clear_active",
        } <= names

    @pytest.mark.anyio
    async def test_every_tool_has_a_description(self):
        for tool in await m.mcp.list_tools():
            assert tool.description, f"{tool.name} has no description"


@pytest.fixture
def anyio_backend():
    return "asyncio"
