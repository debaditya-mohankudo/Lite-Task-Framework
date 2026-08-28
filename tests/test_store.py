"""TaskStore tests — round-tripping, querying, and the relational guarantees.

Includes coverage for unlink(), which exists specifically because its absence
in the prior system left stale edges with no way to clear them.
"""
from __future__ import annotations

import pytest

from taskfw.task import ResolutionItem, Task
from taskfw.store import TaskStore


@pytest.fixture
def store(tmp_path):
    s = TaskStore(tmp_path / "t.db")
    yield s
    s.close()


def make(**kw) -> Task:
    return Task(title=kw.pop("title", "A task"), **kw)


class TestRoundTrip:
    def test_save_and_get_preserves_every_field(self, store):
        t = make(
            title="Build the thing",
            motivation="because",
            resolution=[ResolutionItem("step one", True), ResolutionItem("step two")],
            files=["a.py", "b.py"],
            tags=["x", "y"],
            notes="freeform",
            grooming={"risks": [{"text": "r", "graded": None}]},
        )
        store.save(t)
        got = store.get(t.id)
        assert got.title == "Build the thing"
        assert got.motivation == "because"
        assert [r.text for r in got.resolution] == ["step one", "step two"]
        assert got.resolution[0].done is True
        assert got.files == ["a.py", "b.py"]
        assert got.tags == ["x", "y"]
        assert got.notes == "freeform"
        assert got.grooming["risks"][0]["text"] == "r"

    def test_get_missing_returns_none(self, store):
        assert store.get("nope") is None

    def test_save_is_an_upsert(self, store):
        t = make(title="first")
        store.save(t)
        t.title = "second"
        store.save(t)
        assert store.get(t.id).title == "second"
        assert len(store.list(status=None)) == 1

    def test_unknown_keys_are_dropped_not_raised(self):
        """A store written by a newer version stays readable by an older one."""
        t = Task.from_dict({"id": "x", "title": "t", "field_from_the_future": 1})
        assert t.id == "x"

    def test_scalar_columns_track_the_blob(self, store):
        t = make(title="t", epic=True)
        t.status = "blocked"
        store.save(t)
        row = store.conn.execute("SELECT epic, status, title FROM tasks WHERE id=?", (t.id,)).fetchone()
        assert (row["epic"], row["status"], row["title"]) == (1, "blocked", "t")


class TestProgress:
    def test_progress_is_derived(self):
        t = make(resolution=[ResolutionItem("a", True), ResolutionItem("b"), ResolutionItem("c")])
        assert t.progress == (1, 3)

    def test_progress_cannot_drift(self, store):
        t = make(resolution=[ResolutionItem("a")])
        store.save(t)
        got = store.get(t.id)
        got.resolution[0].done = True
        assert got.progress == (1, 1)


class TestListing:
    def test_defaults_to_open_and_blocked(self, store):
        store.save(make(title="o"))
        d = make(title="d")
        d.status = "done"
        store.save(d)
        assert [t.title for t in store.list()] == ["o"]

    def test_filter_by_epic_and_parent(self, store):
        e = make(title="epic", epic=True)
        store.save(e)
        store.save(make(title="child", parent=e.id))
        assert len(store.list(epic=True)) == 1
        assert len(store.list(epic=False)) == 1
        assert len(store.list(epic=None)) == 2
        assert [t.title for t in store.children(e.id)] == ["child"]


class TestSearch:
    def test_finds_by_title_and_motivation(self, store):
        store.save(make(title="migration runner", motivation="additive only"))
        store.save(make(title="unrelated"))
        assert [t.title for t in store.search("migration")] == ["migration runner"]
        assert [t.title for t in store.search("additive")] == ["migration runner"]

    def test_malformed_fts_query_falls_back_instead_of_raising(self, store):
        store.save(make(title="quoted thing"))
        # An unbalanced quote is invalid FTS5 syntax; a user typo must not 500.
        assert store.search('"unbalanced') is not None

    def test_search_reflects_updates(self, store):
        t = make(title="before")
        store.save(t)
        t.title = "after"
        store.save(t)
        assert store.search("before") == []
        assert len(store.search("after")) == 1

    def test_finds_on_partial_term_overlap_not_just_exact_phrase(self, store):
        store.save(make(title="Add hit_count tracking"))
        store.save(make(title="Add usage tracking to memory"))
        titles = [t.title for t in store.search("Add hit_count tracking")]
        assert "Add usage tracking to memory" in titles

    def test_tag_match_ranks_above_body_only_match(self, store):
        # "widget" appears only in the body of one task, only as a tag on the
        # other -- the tag match must rank first despite otherwise-equal titles.
        store.save(make(title="a task", motivation="build a widget for the UI"))
        store.save(make(title="a task", tags=["widget"]))
        results = store.search("widget")
        assert results[0].tags == ["widget"]

    def test_non_tag_query_ranking_is_unaffected_by_tag_weighting(self, store):
        """A query that matches neither task's tags still returns both, most-
        overlapping body first — tag weighting must not break plain search."""
        store.save(make(title="alpha widget gadget"))
        store.save(make(title="alpha widget"))
        results = store.search("alpha widget gadget")
        assert results[0].title == "alpha widget gadget"


class TestEvents:
    def test_events_are_appended_newest_first(self, store):
        t = store.save(make())
        store.add_event(t.id, "first", kind="decision")
        store.add_event(t.id, "second")
        evs = store.events(t.id)
        assert [e["text"] for e in evs] == ["second", "first"]
        assert evs[1]["kind"] == "decision"


class TestEdges:
    def test_link_is_idempotent(self, store):
        a, b = store.save(make(title="a")), store.save(make(title="b"))
        assert store.link(a.id, b.id, "depends_on") is True
        assert store.link(a.id, b.id, "depends_on") is False
        assert len(store.edges(a.id)["outgoing"]) == 1

    def test_edges_report_both_directions(self, store):
        a, b = store.save(make(title="a")), store.save(make(title="b"))
        store.link(a.id, b.id, "depends_on")
        assert store.edges(b.id)["incoming"][0]["from_id"] == a.id

    def test_unlink_removes_a_specific_relation(self, store):
        a, b = store.save(make(title="a")), store.save(make(title="b"))
        store.link(a.id, b.id, "depends_on")
        store.link(a.id, b.id, "relates_to")
        assert store.unlink(a.id, b.id, "depends_on") == 1
        assert [e["rel"] for e in store.edges(a.id)["outgoing"]] == ["relates_to"]

    def test_unlink_without_rel_removes_all(self, store):
        a, b = store.save(make(title="a")), store.save(make(title="b"))
        store.link(a.id, b.id, "depends_on")
        store.link(a.id, b.id, "relates_to")
        assert store.unlink(a.id, b.id) == 2
        assert store.edges(a.id)["outgoing"] == []


class TestCommits:
    def test_commit_capture_is_idempotent(self, store):
        t = store.save(make())
        assert store.add_commit(t.id, "abc123", "/repo") is True
        assert store.add_commit(t.id, "abc123", "/repo") is False
        assert len(store.commits(t.id)) == 1


class TestActiveTask:
    def test_set_get_clear(self, store):
        t = store.save(make())
        assert store.get_active() is None
        store.set_active(t.id)
        assert store.get_active() == t.id
        store.clear_active()
        assert store.get_active() is None

    def test_scopes_are_independent(self, store):
        a, b = store.save(make(title="a")), store.save(make(title="b"))
        store.set_active(a.id, scope="/work/one")
        store.set_active(b.id, scope="/work/two")
        assert store.get_active("/work/one") == a.id
        assert store.get_active("/work/two") == b.id

    def test_set_active_replaces_rather_than_stacks(self, store):
        a, b = store.save(make(title="a")), store.save(make(title="b"))
        store.set_active(a.id)
        store.set_active(b.id)
        assert store.get_active() == b.id
        store.clear_active()
        assert store.get_active() is None

    def test_set_active_already_active_is_a_no_op(self, store):
        t = store.save(make())
        store.set_active(t.id)
        store.set_active(t.id)
        assert store.get_active() == t.id

    def test_clear_active_on_empty_scope_is_a_no_op(self, store):
        store.clear_active()
        assert store.get_active() is None
