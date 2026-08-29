"""Risk primitive tests — the shared shape rule, pinned once.

`taskfw.risk` exists so that `mcp_server._merge_grooming_risks`,
`accuracy._risks`, and `accuracy._RecurrenceGrouper` all coerce and normalise
a risk the same way. These tests pin the two properties that made pulling the
primitives out safe: `coerce` never manufactures an `id` (the merge keys off
its absence), and it never drops an entry (an ungraded risk that vanished is
the failure `accuracy` exists to prevent).
"""
from __future__ import annotations

from taskfw.risk import coerce, normalise_text


class TestNormaliseText:
    def test_collapses_whitespace_case_and_a_trailing_period(self):
        assert normalise_text("  The  Build   Breaks. ") == "the build breaks"

    def test_two_texts_differing_only_in_noise_share_a_key(self):
        assert normalise_text("Grade may reset") == normalise_text("grade may reset.")

    def test_none_and_empty_are_the_empty_key(self):
        assert normalise_text("") == ""
        assert normalise_text(None) == ""  # type: ignore[arg-type]


class TestCoerce:
    def test_a_bare_string_becomes_an_ungraded_risk_dict(self):
        assert coerce("network may flake") == {"text": "network may flake", "graded": None}

    def test_a_dict_is_copied_verbatim_with_no_key_added(self):
        raw = {"text": "x", "graded": "materialized"}
        out = coerce(raw)
        assert out == raw
        assert "id" not in out  # absence is load-bearing to the merge
        assert out is not raw  # a copy, so callers can mutate freely

    def test_a_dict_that_already_has_an_id_keeps_it(self):
        assert coerce({"id": "abc", "text": "y", "graded": None})["id"] == "abc"

    def test_a_malformed_entry_still_carries_text_rather_than_vanishing(self):
        assert coerce(42) == {"text": "42", "graded": None}
        assert coerce(None) == {"text": "None", "graded": None}
