"""Loop memory tests — the lessons introspection produces, and their standing.

Two properties carry the design and both fail silently if they regress.

Standing is DERIVED from the confirm/contradict links, never stored. A stored
confidence would agree with whatever was typed, which is the failure
taskfw.accuracy already exists to prevent — the same mistake twice would be
worse than the first.

A superseded memory leaves recall by default. Stale knowledge that keeps
surfacing is worse than none, because it reads as current, and a test is the
only thing that would notice it coming back.
"""
from __future__ import annotations

import pytest

from taskfw.memory import KINDS, MIN_TEXT, MemoryStore, Rejected
from taskfw.models import Task
from taskfw.store import TaskStore

LESSON = "FTS5 is a compile-time option, so search must degrade to LIKE rather than fail."
OTHER = "Deriving a value on read removes the reconciliation logic entirely."


@pytest.fixture
def stores(tmp_path):
    """Task and memory stores over ONE connection, as the server wires them."""
    tasks = TaskStore(tmp_path / "t.db")
    memories = MemoryStore(conn=tasks.conn)
    yield tasks, memories
    tasks.close()


@pytest.fixture
def task(stores):
    return stores[0].save(Task(title="A finished task")).id


class TestRecord:
    def test_records_and_cites_its_source(self, stores, task):
        m = stores[1].record("degrade-fts-to-like", LESSON, task)
        assert m["slug"] == "degrade-fts-to-like"
        assert m["learned_from"] == [task]
        assert m["standing"] == "unverified"

    def test_re_recording_updates_text_and_adds_the_new_source(self, stores, task):
        store = stores[1]
        second = stores[0].save(Task(title="Another")).id
        store.record("slug-here", LESSON, task)
        m = store.record("slug-here", OTHER, second)
        assert m["text"] == OTHER
        assert set(m["learned_from"]) == {task, second}

    def test_rejects_a_non_kebab_slug(self, stores, task):
        with pytest.raises(Rejected, match="kebab-case"):
            stores[1].record("Not A Slug", LESSON, task)

    def test_rejects_an_unknown_kind(self, stores, task):
        with pytest.raises(Rejected, match="Unknown kind"):
            stores[1].record("a-slug", LESSON, task, kind="opinion")

    def test_rejects_a_label_masquerading_as_a_lesson(self, stores, task):
        """A memory shorter than a sentence is a tag, not knowledge."""
        with pytest.raises(Rejected, match=str(MIN_TEXT)):
            stores[1].record("a-slug", "be careful", task)

    @pytest.mark.parametrize("kind", KINDS)
    def test_every_declared_kind_is_accepted(self, stores, task, kind):
        assert stores[1].record(f"slug-{kind}", LESSON, task, kind=kind)["kind"] == kind


class TestStanding:
    """Derived from links on every read — never stored, never self-asserted."""

    def test_unverified_until_something_confirms_it(self, stores, task):
        assert stores[1].record("a-slug", LESSON, task)["standing"] == "unverified"

    def test_confirmed_when_a_later_task_agrees(self, stores, task):
        tasks, memories = stores
        later = tasks.save(Task(title="Later")).id
        memories.record("a-slug", LESSON, task)
        memories.link("a-slug", later, "confirmed_by")
        assert memories.get("a-slug")["standing"] == "confirmed"

    def test_contradicted_when_a_later_task_disagrees(self, stores, task):
        tasks, memories = stores
        later = tasks.save(Task(title="Later")).id
        memories.record("a-slug", LESSON, task)
        memories.link("a-slug", later, "contradicted_by")
        assert memories.get("a-slug")["standing"] == "contradicted"

    def test_disputed_is_reported_rather_than_resolved(self, stores, task):
        """Both sides recorded, neither silently discarded.

        The store cannot know which side is right; returning a disputed lesson
        as settled would be the omission-looks-like-absence failure again.
        """
        tasks, memories = stores
        yes = tasks.save(Task(title="Agrees")).id
        no = tasks.save(Task(title="Disagrees")).id
        memories.record("a-slug", LESSON, task)
        memories.link("a-slug", yes, "confirmed_by")
        memories.link("a-slug", no, "contradicted_by")
        m = memories.get("a-slug")
        assert m["standing"] == "disputed"
        assert m["confirmed_by"] == [yes] and m["contradicted_by"] == [no]

    def test_confirming_twice_from_one_task_does_not_inflate_standing(self, stores, task):
        tasks, memories = stores
        later = tasks.save(Task(title="Later")).id
        memories.record("a-slug", LESSON, task)
        assert memories.link("a-slug", later, "confirmed_by") is True
        assert memories.link("a-slug", later, "confirmed_by") is False
        assert memories.get("a-slug")["confirmed_by"] == [later]

    def test_rejects_an_unknown_relation(self, stores, task):
        stores[1].record("a-slug", LESSON, task)
        with pytest.raises(Rejected, match="Unknown relation"):
            stores[1].link("a-slug", task, "sort-of-agrees")

    def test_linking_an_unknown_memory_is_rejected(self, stores, task):
        with pytest.raises(Rejected, match="No memory"):
            stores[1].link("never-recorded", task, "confirmed_by")


class TestSupersede:
    def test_superseded_memory_leaves_recall_by_default(self, stores, task):
        memories = stores[1]
        memories.record("old-lesson", LESSON, task)
        memories.record("new-lesson", OTHER, task)
        memories.supersede("old-lesson", "new-lesson")
        assert [m["slug"] for m in memories.recall()] == ["new-lesson"]

    def test_but_is_still_reachable_on_request(self, stores, task):
        memories = stores[1]
        memories.record("old-lesson", LESSON, task)
        memories.record("new-lesson", OTHER, task)
        memories.supersede("old-lesson", "new-lesson")
        slugs = {m["slug"] for m in memories.recall(include_superseded=True)}
        assert slugs == {"old-lesson", "new-lesson"}

    def test_the_row_survives_and_names_its_replacement(self, stores, task):
        memories = stores[1]
        memories.record("old-lesson", LESSON, task)
        memories.record("new-lesson", OTHER, task)
        m = memories.supersede("old-lesson", "new-lesson")
        assert m["superseded_by"] == "new-lesson"
        assert m["standing"] == "superseded"

    def test_a_memory_cannot_supersede_itself(self, stores, task):
        stores[1].record("a-slug", LESSON, task)
        with pytest.raises(Rejected, match="cannot supersede itself"):
            stores[1].supersede("a-slug", "a-slug")

    def test_superseding_by_an_unknown_memory_is_rejected(self, stores, task):
        stores[1].record("a-slug", LESSON, task)
        with pytest.raises(Rejected, match="No memory"):
            stores[1].supersede("a-slug", "does-not-exist")


class TestForget:
    def test_deletes_outright(self, stores, task):
        stores[1].record("a-slug", LESSON, task)
        assert stores[1].forget("a-slug") is True
        assert stores[1].get("a-slug") is None

    def test_forgetting_a_missing_memory_is_false_not_an_error(self, stores):
        assert stores[1].forget("never-existed") is False

    def test_links_do_not_outlive_the_memory(self, stores, task):
        """No orphaned standing pointing at a lesson that no longer exists."""
        tasks, memories = stores
        memories.record("a-slug", LESSON, task)
        memories.link("a-slug", task, "confirmed_by")
        memories.forget("a-slug")
        rows = tasks.conn.execute(
            "SELECT COUNT(*) c FROM memory_links WHERE slug='a-slug'"
        ).fetchone()
        assert rows["c"] == 0


class TestRecall:
    def test_finds_by_text(self, stores, task):
        stores[1].record("degrade-fts", LESSON, task)
        stores[1].record("derive-on-read", OTHER, task)
        assert [m["slug"] for m in stores[1].recall("compile-time")] == ["degrade-fts"]

    def test_finds_by_slug(self, stores, task):
        stores[1].record("degrade-fts", LESSON, task)
        assert [m["slug"] for m in stores[1].recall("degrade")] == ["degrade-fts"]

    def test_filters_by_kind(self, stores, task):
        stores[1].record("a-constraint", LESSON, task, kind="constraint")
        stores[1].record("a-technique", OTHER, task, kind="technique")
        assert [m["slug"] for m in stores[1].recall(kind="technique")] == ["a-technique"]

    def test_empty_query_lists_recent(self, stores, task):
        stores[1].record("one-lesson", LESSON, task)
        stores[1].record("two-lesson", OTHER, task)
        assert len(stores[1].recall()) == 2

    def test_no_match_returns_nothing(self, stores, task):
        stores[1].record("a-slug", LESSON, task)
        assert stores[1].recall("nothing matches this") == []

    def test_malformed_query_falls_back_instead_of_raising(self, stores, task):
        """A bad FTS query is user input, not proof there are no memories."""
        stores[1].record("a-slug", LESSON, task)
        assert stores[1].recall('unbalanced "quote') is not None

    def test_limit_is_respected(self, stores, task):
        for i in range(5):
            stores[1].record(f"lesson-{i}", LESSON, task)
        assert len(stores[1].recall(limit=2)) == 2


class TestUsageTracking:
    def test_record_starts_at_zero_hits(self, stores, task):
        memory = stores[1].record("a-slug", LESSON, task)
        assert memory["hit_count"] == 0
        assert memory["last_hit"] is None

    def test_recall_increments_hit_count(self, stores, task):
        stores[1].record("degrade-fts", LESSON, task)
        stores[1].recall("degrade")
        memory = stores[1].recall("degrade")[0]
        assert memory["hit_count"] == 2

    def test_recall_sets_last_hit(self, stores, task):
        stores[1].record("degrade-fts", LESSON, task)
        assert stores[1].recall("degrade")[0]["last_hit"] is not None

    def test_get_does_not_increment_hit_count(self, stores, task):
        stores[1].record("a-slug", LESSON, task)
        stores[1].get("a-slug")
        stores[1].get("a-slug")
        assert stores[1].get("a-slug")["hit_count"] == 0

    def test_each_result_in_a_multi_memory_recall_gets_a_hit(self, stores, task):
        stores[1].record("one-lesson", LESSON, task)
        stores[1].record("two-lesson", OTHER, task)
        stores[1].recall()
        assert stores[1].get("one-lesson")["hit_count"] == 1
        assert stores[1].get("two-lesson")["hit_count"] == 1
