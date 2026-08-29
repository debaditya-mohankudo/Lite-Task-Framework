"""tasks__context tests — the contract that replaces prompt injection.

These pin section presence, ordering, and the trim behaviour. Trimming is the
part most likely to regress silently: a bundle that quietly drops commits looks
identical to one for a task with no commits, which is why `truncated` exists
and is asserted here.
"""
from __future__ import annotations

import pytest

from taskfw.context import (
    CHAR_BUDGET, MAX_EDGES, MAX_LESSONS, TRIM_ORDER, _BUNDLE_SKELETON, _size, _build_context,
    _related_candidates, TaskContext,
)
from taskfw.memory import MemoryStore
from taskfw.task import ResolutionItem, Task
from taskfw.store import TaskStore


@pytest.fixture
def store(tmp_path):
    s = TaskStore(tmp_path / "t.db")
    yield s
    s.close()


@pytest.fixture
def populated(store):
    epic = store.save(Task(title="The epic", epic=True))
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
        assert "error" in _build_context(store, "nope")


class TestSummary:
    def test_summary_has_identity_and_open_items_only(self, populated):
        c = _build_context(populated["store"], populated["task"].id, verbosity="summary")
        assert c["task"]["title"] == "Do the thing"
        assert c["task"]["progress"] == {"done": 1, "total": 2}
        assert c["task"]["open_items"] == ["step two"]
        for absent in ("decisions", "grooming", "graph", "commits", "related"):
            assert absent not in c

    def test_summary_is_much_smaller_than_full(self, populated):
        import json
        tid = populated["task"].id
        s = len(json.dumps(_build_context(populated["store"], tid, "summary"), default=str))
        f = len(json.dumps(_build_context(populated["store"], tid, "full"), default=str))
        assert s < f


class TestFullBundle:
    def test_every_section_is_present(self, populated):
        c = _build_context(populated["store"], populated["task"].id)
        for section in ("task", "decisions", "grooming", "graph", "commits", "related"):
            assert section in c

    def test_section_order_is_part_of_the_contract(self, populated):
        c = _build_context(populated["store"], populated["task"].id)
        keys = [k for k in c if k in ("task", "decisions", "grooming", "graph", "commits")]
        assert keys == ["task", "decisions", "grooming", "graph", "commits"]

    def test_only_decisions_appear_in_decisions(self, populated):
        c = _build_context(populated["store"], populated["task"].id)
        texts = [d["text"] for d in c["decisions"]]
        assert "chose X over Y" in texts
        assert "just a note" not in texts

    def test_graph_carries_parent_children_and_edges(self, populated):
        c = _build_context(populated["store"], populated["task"].id)
        assert c["graph"]["parent"]["id"] == populated["epic"].id
        assert [ch["id"] for ch in c["graph"]["children"]] == [populated["child"].id]
        assert c["graph"]["edges"]["outgoing"][0]["to_id"] == populated["epic"].id

    def test_commits_are_an_exact_per_task_lookup(self, populated):
        c = _build_context(populated["store"], populated["task"].id)
        assert [x["sha"] for x in c["commits"]] == ["abc123"]

    def test_task_section_is_complete(self, populated):
        c = _build_context(populated["store"], populated["task"].id)["task"]
        assert c["motivation"] == "because reasons"
        assert c["files"] == ["a.py"] and c["tags"] == ["x"] and c["notes"] == "a note"
        assert [r["done"] for r in c["resolution"]] == [True, False]

    def test_related_excludes_the_task_itself(self, store):
        a = store.save(Task(title="migration runner"))
        store.save(Task(title="migration runner two"))
        c = _build_context(store, a.id)
        assert a.id not in [r["id"] for r in c["related"]]

    def test_related_matches_on_partial_word_overlap(self, store):
        """A shared word need not appear as a contiguous phrase to match.

        Under the pre-fix whole-title phrase query, "Add hit_count tracking"
        would never match "Add usage tracking to memory" — the words "Add"
        and "tracking" aren't consecutive across the two titles. OR-ing terms
        fixes exactly this case.
        """
        a = store.save(Task(title="Add hit_count tracking"))
        b = store.save(Task(title="Add usage tracking to memory"))
        c = _build_context(store, a.id)
        assert b.id in [r["id"] for r in c["related"]]

    def test_related_floor_rejects_a_candidate_with_no_meaningful_overlap(self, store):
        """A candidate ranked only by incidental body-text overlap, with zero
        title or tag overlap, must not surface at all.

        Reproduces the live case from task:1f1e48e2's grooming: task 0fecd30e
        ("Remove LLM from certificate renewal flow") appeared as related to
        several genuinely unrelated tasks on shared short/common words alone.
        """
        a = store.save(Task(
            title="Grade the orphaned risks on finished tasks",
            tags=["task-framework", "feedback-loop", "introspection"],
        ))
        store.save(Task(
            title="Remove LLM from certificate renewal flow — fully deterministic",
            tags=["project:acme-certificate-lifecycle-agent", "llm", "renewal",
                  "agent", "deterministic", "certificate", "planner"],
        ))
        c = _build_context(store, a.id)
        assert c["related"] == []

    def test_related_floor_does_not_empty_out_genuine_matches(self, store):
        a = store.save(Task(title="Fix the sqlite migration path"))
        b = store.save(Task(title="Sqlite migration path is broken on restore"))
        c = _build_context(store, a.id)
        assert b.id in [r["id"] for r in c["related"]]

    def test_related_floor_ignores_date_stamped_tag_collisions(self, store):
        """Two tasks tagged with the same review date share no real topic —
        the floor must not treat the shared digits as overlap."""
        a = store.save(Task(title="Audit the billing export job", tags=["2026-08-23"]))
        store.save(Task(title="Rotate the deploy signing key", tags=["2026-08-23"]))
        c = _build_context(store, a.id)
        assert c["related"] == []

    def test_related_still_matches_an_exact_title_phrase(self, store):
        a = store.save(Task(title="degrade FTS to LIKE"))
        b = store.save(Task(title="degrade FTS to LIKE gracefully"))
        c = _build_context(store, a.id)
        assert b.id in [r["id"] for r in c["related"]]

    def test_keys_match_skeleton_on_a_non_truncating_bundle(self, populated):
        """A field added/removed/renamed in build_context's return, without a
        matching change to _BUNDLE_SKELETON, must fail here rather than only
        if some other test happens to touch that field.

        Uses `populated` specifically because it stays under CHAR_BUDGET — the
        3 conditional keys (edges_truncated, truncated, grooming_truncated)
        are deliberately absent from the skeleton, so an equality assertion
        only holds on a bundle where none of them fired.
        """
        c = _build_context(populated["store"], populated["task"].id)
        assert set(c.keys()) == set(_BUNDLE_SKELETON.keys())


class TestBudget:
    def test_small_bundle_is_not_truncated(self, populated):
        assert "truncated" not in _build_context(populated["store"], populated["task"].id)

    def test_oversized_bundle_drops_sections_and_says_so(self, store):
        epic = store.save(Task(title="epic", epic=True))
        big = store.save(Task(title="huge", parent=epic.id, motivation="x" * (CHAR_BUDGET // 2)))
        for i in range(40):
            store.add_event(big.id, f"decision {i} " + "y" * 400, kind="decision")
        for i in range(30):
            store.add_commit(big.id, f"sha{i:040d}", "/repo")
        c = _build_context(store, big.id)
        assert c.get("truncated"), "oversized bundle reported no truncation"
        # Least useful sections go first.
        assert c["truncated"][0] in TRIM_ORDER

    def test_the_task_itself_is_never_trimmed(self, store):
        t = store.save(Task(title="huge", motivation="x" * (CHAR_BUDGET * 2)))
        c = _build_context(store, t.id)
        assert c["task"]["title"] == "huge"
        assert c["task"]["motivation"]

    def test_truncated_distinguishes_empty_from_omitted(self, populated):
        """An absent section and a dropped one must not look the same."""
        c = _build_context(populated["store"], populated["task"].id)
        assert c["commits"], "fixture has a commit"
        assert "truncated" not in c


class TestRelevanceFloorHelper:
    """Unit-level pins on _passes_floor, independent of any store — the
    shared predicate _related_candidates and _lessons_for both filter through.
    """

    def test_no_shared_word_fails(self):
        from taskfw.context import _passes_floor
        assert _passes_floor("migration sqlite path", "certificate renewal flow") is False

    def test_one_shared_word_passes(self):
        from taskfw.context import _passes_floor
        assert _passes_floor("migration sqlite path", "restore the sqlite backup") is True

    def test_short_words_do_not_count_as_overlap(self):
        from taskfw.context import _passes_floor
        # "fix" (both) is too short to anchor a match on its own.
        assert _passes_floor("fix a critical bug", "the big fox ran to fix it") is False

    def test_purely_numeric_tokens_do_not_count_as_overlap(self):
        from taskfw.context import _passes_floor
        assert _passes_floor("audit job 2026-08-23", "rotate key 2026-08-23") is False

    def test_empty_query_is_permissive(self):
        from taskfw.context import _passes_floor
        assert _passes_floor("", "anything at all") is True

    def test_query_with_only_short_words_is_permissive(self):
        from taskfw.context import _passes_floor
        assert _passes_floor("fix a bug", "totally unrelated content here") is True


class TestGroomingTrim:
    """Grooming is a dict, not a list — it can't be whole-section-dropped
    without wasting the budget it would take to keep only its least useful
    fields. See task:1c31a060.
    """

    def _oversized_grooming_task(self, store):
        # Sized so the bundle is only slightly over CHAR_BUDGET and dropping
        # the least-useful field or two brings it back under — mirroring
        # task:1c31a060's reproduction case (grooming=8371, total bundle
        # over budget only once other sections are added in).
        epic = store.save(Task(title="epic", epic=True))
        return store.save(Task(
            title="huge grooming", parent=epic.id,
            grooming={
                "clarifications": ["c" * 1500 for _ in range(3)],
                "open_questions": [{"question": "q" * 2500, "blocking": True}],
                "risks": ["r" * 2000 for _ in range(2)],
                "hidden_assumptions": ["h" * 1500],
                "prior_art": ["p" * 800],
                "suggested_improvements": ["s" * 2000],
            },
        ))

    def test_grooming_only_over_budget_keeps_grooming_not_dropped_whole(self, store):
        t = self._oversized_grooming_task(store)
        c = _build_context(store, t.id)
        assert c["grooming"], "grooming should survive trimmed, not empty"
        assert "grooming" not in c.get("truncated", [])
        assert "grooming_truncated" in c

    def test_least_useful_grooming_fields_drop_first(self, store):
        t = self._oversized_grooming_task(store)
        c = _build_context(store, t.id)
        assert "suggested_improvements" in c["grooming_truncated"]
        # clarifications are the highest-value field; kept if anything is.
        if "clarifications" not in c["grooming_truncated"]:
            assert "clarifications" in c["grooming"]

    def test_grooming_bundle_no_longer_leaves_budget_unspent(self, store):
        t = self._oversized_grooming_task(store)
        c = _build_context(store, t.id)
        # Whole-section dropping would have zeroed ~8000+ chars of grooming
        # to save a few hundred over budget. Field trimming should land much
        # closer to the ceiling instead of far under it.
        assert _size(c) > CHAR_BUDGET * 0.7, "budget still going largely unspent"

    def test_an_unlisted_grooming_field_falls_back_to_whole_drop(self, store):
        """A grooming key outside GROOMING_TRIM_ORDER (future schema, direct API
        write, older data) is never touched by _trim_grooming's field-by-field
        loop, so it alone can keep bundle["grooming"] non-empty even after every
        known field has been shrunk. _enforce_budget must still fall back to
        dropping the section whole rather than returning an over-budget bundle."""
        epic = store.save(Task(title="epic", epic=True))
        t = store.save(Task(
            title="unlisted field", parent=epic.id,
            grooming={"an_unrecognised_field": "x" * (CHAR_BUDGET * 3)},
        ))
        c = _build_context(store, t.id)
        assert c["grooming"] == {}
        assert "grooming" in c["truncated"]
        assert _size(c) <= CHAR_BUDGET

    def test_grooming_that_cannot_shrink_enough_falls_back_to_whole_drop(self, store):
        epic = store.save(Task(title="epic", epic=True))
        t = store.save(Task(
            title="unshrinkable", parent=epic.id,
            grooming={"clarifications": ["c" * (CHAR_BUDGET * 3)]},
        ))
        c = _build_context(store, t.id)
        assert c["grooming"] == {}
        assert "grooming" in c["truncated"]
        assert "grooming_truncated" not in c


class TestEdgesCap:
    def test_edges_capped_per_direction(self, store):
        task = store.save(Task(title="hub"))
        for i in range(MAX_EDGES + 3):
            other = store.save(Task(title=f"dep {i}"))
            store.link(task.id, other.id, "depends_on")
        for i in range(MAX_EDGES + 2):
            other = store.save(Task(title=f"blocker {i}"))
            store.link(other.id, task.id, "blocks")
        c = _build_context(store, task.id)
        assert len(c["graph"]["edges"]["outgoing"]) == MAX_EDGES
        assert len(c["graph"]["edges"]["incoming"]) == MAX_EDGES
        assert c["edges_truncated"] == {"outgoing": 3, "incoming": 2}

    def test_no_edges_truncated_field_when_under_cap(self, populated):
        c = _build_context(populated["store"], populated["task"].id)
        assert "edges_truncated" not in c

    def test_edges_truncated_cleared_if_graph_dropped_for_budget(self, store):
        epic = store.save(Task(title="epic", epic=True))
        big = store.save(Task(title="huge", parent=epic.id, motivation="x" * (CHAR_BUDGET // 2)))
        for i in range(40):
            store.add_event(big.id, f"decision {i} " + "y" * 400, kind="decision")
        for i in range(MAX_EDGES + 3):
            other = store.save(Task(title=f"dep {i}"))
            store.link(big.id, other.id, "depends_on")
        c = _build_context(store, big.id)
        if "graph" in c.get("truncated", []):
            assert "edges_truncated" not in c


class TestLessons:
    """The read path that makes loop memory load-bearing instead of write-only.

    Doc 05 tells introspection to read lessons back when grooming, but nothing
    guaranteed that read until this section existed. These pin the guarantee.
    """

    @pytest.fixture
    def memory(self, store):
        return MemoryStore(conn=store.conn)

    def test_matching_memory_comes_back_in_the_bundle(self, store, memory):
        t = store.save(Task(title="Fix the sqlite migration path"))
        memory.record("migrations-are-additive", task_id=t.id, kind="constraint",
                      text="Sqlite migration steps must be additive; a rewrite loses rows.")
        c = _build_context(store, t.id, memory=memory)
        assert [m["slug"] for m in c["lessons"]] == ["migrations-are-additive"]

    def test_no_match_returns_an_empty_section_not_a_missing_one(self, store, memory):
        t = store.save(Task(title="Fix the sqlite migration path"))
        memory.record("unrelated-lesson", task_id=t.id, kind="technique",
                      text="Espresso extraction favours a coarser burr grind setting.")
        c = _build_context(store, t.id, memory=memory)
        assert c["lessons"] == []
        assert "lessons" in c, "an empty section must still be present"

    def test_a_disputed_lesson_arrives_marked_disputed(self, store, memory):
        t = store.save(Task(title="Fix the sqlite migration path"))
        memory.record("migrations-are-additive", task_id=t.id, kind="constraint",
                      text="Sqlite migration steps must be additive; a rewrite loses rows.")
        memory.link("migrations-are-additive", store.save(Task(title="a")).id, "confirmed_by")
        memory.link("migrations-are-additive", store.save(Task(title="b")).id, "contradicted_by")
        c = _build_context(store, t.id, memory=memory)
        assert c["lessons"][0]["standing"] == "disputed"

    def test_superseded_lessons_stay_out(self, store, memory):
        t = store.save(Task(title="Fix the sqlite migration path"))
        memory.record("old-migration-rule", task_id=t.id,
                      text="Sqlite migration steps must be additive; a rewrite loses rows.")
        memory.record("new-migration-rule", task_id=t.id,
                      text="Sqlite migration steps are additive and versioned per table.")
        memory.supersede("old-migration-rule", "new-migration-rule")
        slugs = [m["slug"] for m in _build_context(store, t.id, memory=memory)["lessons"]]
        assert "old-migration-rule" not in slugs

    def test_capped_at_max_lessons(self, store, memory):
        t = store.save(Task(title="Fix the sqlite migration path"))
        for i in range(MAX_LESSONS + 3):
            memory.record(f"migration-lesson-{i}", task_id=t.id,
                          text=f"Sqlite migration rule number {i}; additive steps only always.")
        c = _build_context(store, t.id, memory=memory)
        assert len(c["lessons"]) == MAX_LESSONS

    def test_assembling_a_bundle_does_not_bump_hit_count(self, store, memory):
        """hit_count answers 'what does anyone reach for deliberately'.

        Bundle assembly is not a deliberate reach, so counting it here would
        leave the counter measuring context calls with no way to separate the
        two afterwards.
        """
        t = store.save(Task(title="Fix the sqlite migration path"))
        memory.record("migrations-are-additive", task_id=t.id,
                      text="Sqlite migration steps must be additive; a rewrite loses rows.")
        _build_context(store, t.id, memory=memory)
        _build_context(store, t.id, memory=memory)
        assert memory.get("migrations-are-additive")["hit_count"] == 0
        # ...while a deliberate recall still counts.
        memory.recall("sqlite migration")
        assert memory.get("migrations-are-additive")["hit_count"] == 1

    def test_summary_verbosity_carries_no_lessons(self, store, memory):
        t = store.save(Task(title="Fix the sqlite migration path"))
        memory.record("migrations-are-additive", task_id=t.id,
                      text="Sqlite migration steps must be additive; a rewrite loses rows.")
        assert "lessons" not in _build_context(store, t.id, "summary", memory=memory)

    def test_lessons_floor_rejects_a_slug_with_no_meaningful_overlap(self, store, memory):
        """Pre-floor, this memory always filled a slot on relevance ranking
        alone, however weak the match — see test_no_match_returns_an_empty_section_not_a_missing_one,
        which pins the same scenario but predates the floor existing at all."""
        t = store.save(Task(title="Fix the sqlite migration path"))
        memory.record("unrelated-lesson", task_id=t.id, kind="technique",
                      text="Espresso extraction favours a coarser burr grind setting.")
        c = _build_context(store, t.id, memory=memory)
        assert c["lessons"] == []

    def test_lessons_floor_does_not_empty_out_a_genuine_match(self, store, memory):
        t = store.save(Task(title="Fix the sqlite migration path"))
        memory.record("migrations-are-additive", task_id=t.id, kind="constraint",
                      text="Sqlite migration steps must be additive; a rewrite loses rows.")
        c = _build_context(store, t.id, memory=memory)
        assert [m["slug"] for m in c["lessons"]] == ["migrations-are-additive"]

    def test_lessons_is_trimmed_after_related_and_before_commits(self):
        i = TRIM_ORDER.index("lessons")
        assert TRIM_ORDER[i - 1] == "related"
        assert TRIM_ORDER[i + 1] == "commits"

    def test_dropped_lessons_are_named_in_truncated(self, store, memory):
        """An omitted lessons section must not read as an absent one."""
        big = store.save(Task(title="Fix the sqlite migration path",
                              motivation="x" * (CHAR_BUDGET // 2)))
        memory.record("migrations-are-additive", task_id=big.id,
                      text="Sqlite migration steps must be additive; a rewrite loses rows.")
        for i in range(40):
            store.add_event(big.id, f"decision {i} " + "y" * 400, kind="decision")
        c = _build_context(store, big.id, memory=memory)
        assert "lessons" in c["truncated"]
        assert c["lessons"] == []


class TestTaskContext:
    """TaskContext (task:01fba67f) is the interface mcp_server depends on
    instead of build_context/related_candidates directly. It is deliberately
    thin — these pin the delegation, not new behaviour, plus the decoupling
    contract itself (concept_store's pull-context-bundle and
    mcp-portable-interface both now claim mcp_server has no direct import of
    either function)."""

    def test_bundle_delegates_to_build_context(self, populated):
        store = populated["store"]
        tid = populated["task"].id
        assert TaskContext(store).bundle(tid) == _build_context(store, tid, "full", memory=None)

    def test_bundle_passes_verbosity_and_memory_through(self, store):
        t = store.save(Task(title="Fix the sqlite migration path"))
        memory = MemoryStore(conn=store.conn)
        memory.record("migrations-are-additive", task_id=t.id,
                      text="Sqlite migration steps must be additive; a rewrite loses rows.")
        via_facade = TaskContext(store, memory=memory).bundle(t.id, "summary")
        direct = _build_context(store, t.id, "summary", memory=memory)
        assert via_facade == direct

    def test_related_delegates_to_related_candidates(self, populated):
        store = populated["store"]
        task = populated["task"]
        assert TaskContext(store).related(task) == _related_candidates(store, task)

    def test_mcp_server_reaches_context_only_through_task_context(self):
        import inspect

        from taskfw import mcp_server
        src = inspect.getsource(mcp_server)
        assert "TaskContext" in src, "mcp_server must reach context assembly through TaskContext"
        assert "_build_context(" not in src, (
            "mcp_server must call TaskContext(...).bundle(), not _build_context() directly — "
            "see the pull-context-bundle and mcp-portable-interface concepts"
        )
        assert "_related_candidates(" not in src
