"""tasks__context tests — the contract that replaces prompt injection.

These pin section presence, ordering, and the trim behaviour. Trimming is the
part most likely to regress silently: a bundle that quietly drops commits looks
identical to one for a task with no commits, which is why `truncated` exists
and is asserted here.
"""
from __future__ import annotations

import pytest

from taskfw.context import CHAR_BUDGET, TRIM_ORDER, build_context
from taskfw.models import ResolutionItem, Task
from taskfw.store import TaskStore


@pytest.fixture
def store(tmp_path):
    s = TaskStore(tmp_path / "t.db")
    yield s
    s.close()


@pytest.fixture
def populated(store):
    epic = store.save(Task(title="The epic", type="epic"))
    task = store.save(Task(
        title="Do the thing", parent=epic.id, motivation="because reasons",
        resolution=[ResolutionItem("step one", True), ResolutionItem("step two")],
        files=["a.py"], tags=["x"], notes="a note",
        grooming={"risks": [{"text": "something", "graded": None}]},
    ))
    child = store.save(Task(title="A subtask", parent=task.id))
    store.add_event(task.id, "chose X over Y", kind="decision")
    store.add_event(task.id, "just a note", kind="note")
    store.add_commit(task.id, "abc123", "/repo")
    store.link(task.id, epic.id, "depends_on")
    return {"store": store, "epic": epic, "task": task, "child": child}


class TestMissing:
    def test_unknown_task_returns_error(self, store):
        assert "error" in build_context(store, "nope")


class TestSummary:
    def test_summary_has_identity_and_open_items_only(self, populated):
        c = build_context(populated["store"], populated["task"].id, verbosity="summary")
        assert c["task"]["title"] == "Do the thing"
        assert c["task"]["progress"] == {"done": 1, "total": 2}
        assert c["task"]["open_items"] == ["step two"]
        for absent in ("decisions", "grooming", "graph", "commits", "related"):
            assert absent not in c

    def test_summary_is_much_smaller_than_full(self, populated):
        import json
        tid = populated["task"].id
        s = len(json.dumps(build_context(populated["store"], tid, "summary"), default=str))
        f = len(json.dumps(build_context(populated["store"], tid, "full"), default=str))
        assert s < f


class TestFullBundle:
    def test_every_section_is_present(self, populated):
        c = build_context(populated["store"], populated["task"].id)
        for section in ("task", "decisions", "grooming", "graph", "commits", "related"):
            assert section in c

    def test_section_order_is_part_of_the_contract(self, populated):
        c = build_context(populated["store"], populated["task"].id)
        keys = [k for k in c if k in ("task", "decisions", "grooming", "graph", "commits")]
        assert keys == ["task", "decisions", "grooming", "graph", "commits"]

    def test_only_decisions_appear_in_decisions(self, populated):
        c = build_context(populated["store"], populated["task"].id)
        texts = [d["text"] for d in c["decisions"]]
        assert "chose X over Y" in texts
        assert "just a note" not in texts

    def test_graph_carries_parent_children_and_edges(self, populated):
        c = build_context(populated["store"], populated["task"].id)
        assert c["graph"]["parent"]["id"] == populated["epic"].id
        assert [ch["id"] for ch in c["graph"]["children"]] == [populated["child"].id]
        assert c["graph"]["edges"]["outgoing"][0]["to_id"] == populated["epic"].id

    def test_commits_are_an_exact_per_task_lookup(self, populated):
        c = build_context(populated["store"], populated["task"].id)
        assert [x["sha"] for x in c["commits"]] == ["abc123"]

    def test_task_section_is_complete(self, populated):
        c = build_context(populated["store"], populated["task"].id)["task"]
        assert c["motivation"] == "because reasons"
        assert c["files"] == ["a.py"] and c["tags"] == ["x"] and c["notes"] == "a note"
        assert [r["done"] for r in c["resolution"]] == [True, False]

    def test_related_excludes_the_task_itself(self, store):
        a = store.save(Task(title="migration runner"))
        store.save(Task(title="migration runner two"))
        c = build_context(store, a.id)
        assert a.id not in [r["id"] for r in c["related"]]


class TestBudget:
    def test_small_bundle_is_not_truncated(self, populated):
        assert "truncated" not in build_context(populated["store"], populated["task"].id)

    def test_oversized_bundle_drops_sections_and_says_so(self, store):
        epic = store.save(Task(title="epic", type="epic"))
        big = store.save(Task(title="huge", parent=epic.id, motivation="x" * (CHAR_BUDGET // 2)))
        for i in range(40):
            store.add_event(big.id, f"decision {i} " + "y" * 400, kind="decision")
        for i in range(30):
            store.add_commit(big.id, f"sha{i:040d}", "/repo")
        c = build_context(store, big.id)
        assert c.get("truncated"), "oversized bundle reported no truncation"
        # Least useful sections go first.
        assert c["truncated"][0] in TRIM_ORDER

    def test_the_task_itself_is_never_trimmed(self, store):
        t = store.save(Task(title="huge", motivation="x" * (CHAR_BUDGET * 2)))
        c = build_context(store, t.id)
        assert c["task"]["title"] == "huge"
        assert c["task"]["motivation"]

    def test_truncated_distinguishes_empty_from_omitted(self, populated):
        """An absent section and a dropped one must not look the same."""
        c = build_context(populated["store"], populated["task"].id)
        assert c["commits"], "fixture has a commit"
        assert "truncated" not in c
