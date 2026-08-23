"""Task's own contracts — serialisation and derived fields, pinned directly.

These were previously only proven by proxy through store.py's save/load and
search tests, which check outcomes (a search matched, a reload round-tripped)
rather than Task's own documented rules. Pure Task construction and
(de)serialisation, so — like test_lifecycle.py — this needs no store.
"""
from __future__ import annotations

from taskfw.task import ResolutionItem, Task


class TestSearchText:
    def test_flattens_title_motivation_notes_tags_files_and_resolution(self):
        t = Task(
            title="ship it",
            motivation="because",
            notes="a note",
            tags=["urgent", "backend"],
            files=["a.py", "b.py"],
            resolution=[ResolutionItem(text="do the thing"), ResolutionItem(text="check it", done=True)],
        )
        text = t.search_text()
        for expected in ("ship it", "because", "a note", "urgent backend",
                          "a.py b.py", "do the thing", "check it"):
            assert expected in text

    def test_falsy_fields_are_excluded_rather_than_appearing_blank(self):
        t = Task(title="only a title")
        text = t.search_text()
        assert text == "only a title"

    def test_resolution_item_text_is_included_even_when_done(self):
        t = Task(title="t", resolution=[ResolutionItem(text="finished item", done=True)])
        assert "finished item" in t.search_text()


class TestJSONRoundTrip:
    def test_to_json_and_from_json_preserve_every_field(self):
        t = Task(
            type="epic",
            status="blocked",
            parent="parentid",
            title="t",
            motivation="m",
            resolution=[ResolutionItem(text="x", done=True)],
            files=["f.py"],
            tags=["tag"],
            notes="n",
            grooming={"clarifications": ["c"]},
            introspection=[{"date": "2026-01-01"}],
        )
        restored = Task.from_json(t.to_json())
        assert restored == t

    def test_from_dict_tolerates_unknown_keys(self):
        t = Task.from_dict({"id": "x", "title": "t", "field_from_the_future": 1})
        assert t.id == "x" and t.title == "t"
        assert not hasattr(t, "field_from_the_future")

    def test_from_dict_defaults_missing_keys(self):
        t = Task.from_dict({"title": "t"})
        assert t.status == "open" and t.type == "task" and t.resolution == []

    def test_from_dict_converts_raw_resolution_dicts_to_resolution_items(self):
        t = Task.from_dict({"title": "t", "resolution": [{"text": "x", "done": True}]})
        assert t.resolution == [ResolutionItem(text="x", done=True)]

    def test_from_dict_passes_through_existing_resolution_items_unchanged(self):
        item = ResolutionItem(text="x")
        t = Task.from_dict({"title": "t", "resolution": [item]})
        assert t.resolution == [item]

    def test_from_json_of_none_or_empty_returns_a_default_task(self):
        for t in (Task.from_json(None), Task.from_json("{}")):
            assert t.title == "" and t.status == "open" and t.type == "task"
            assert t.resolution == []


class TestProgress:
    def test_no_resolution_items_is_zero_of_zero(self):
        assert Task(title="t").progress == (0, 0)

    def test_counts_done_versus_total(self):
        t = Task(title="t", resolution=[
            ResolutionItem(text="a", done=True),
            ResolutionItem(text="b", done=False),
            ResolutionItem(text="c", done=True),
        ])
        assert t.progress == (2, 3)

    def test_progress_is_derived_not_stored(self):
        """Mutating resolution changes progress immediately — nothing to keep in sync."""
        t = Task(title="t", resolution=[ResolutionItem(text="a")])
        assert t.progress == (0, 1)
        t.resolution[0].done = True
        assert t.progress == (1, 1)
