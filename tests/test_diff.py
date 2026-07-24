"""Unit tests for concept_store/diff.py."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from concept_store.diff import diff, format_drift, DriftReport
from concept_store.store import ConceptStore

_MOD = "hooks/dispatcher.py"

_OLD = {
    "name": "dispatcher-routes-by-hook-type",
    "module": _MOD,
    "description": "Routes hook events to handler nodes based on event_type.",
    "invariants": ["all hooks must return HookResult"],
    "contracts": ["returns dict or None"],
    "confidence": 0.9,
    "evidence": [f"{_MOD}:42"],
}


def _make_fake_client(concepts: list) -> MagicMock:
    agent = MagicMock()
    agent.complete.return_value = json.dumps(concepts)
    return agent


def _seed_store(tmp_path: Path, concept: dict) -> ConceptStore:
    store = ConceptStore(tmp_path / "concepts.json")
    store.upsert(concept)
    return store


def test_no_drift(tmp_path):
    store = _seed_store(tmp_path, _OLD)
    client = _make_fake_client([_OLD])
    reports = diff([_MOD], tmp_path, store, agent=client)
    assert len(reports) == 1
    assert not reports[0].has_drift


def test_changed_invariant_detected(tmp_path):
    store = _seed_store(tmp_path, _OLD)
    updated = {**_OLD, "invariants": ["hooks may return None for no-op"]}
    client = _make_fake_client([updated])
    reports = diff([_MOD], tmp_path, store, agent=client)
    assert len(reports[0].changed) == 1
    assert reports[0].changed[0]["field"] == "invariants"


def test_new_concept_detected(tmp_path):
    store = _seed_store(tmp_path, _OLD)
    new_concept = {**_OLD, "name": "dispatcher-fallback-chain"}
    client = _make_fake_client([_OLD, new_concept])
    reports = diff([_MOD], tmp_path, store, agent=client)
    assert "dispatcher-fallback-chain" in reports[0].added


def test_dropped_concept_detected(tmp_path):
    store = _seed_store(tmp_path, _OLD)
    client = _make_fake_client([])  # extractor returns nothing for this file
    reports = diff([_MOD], tmp_path, store, agent=client)
    assert "dispatcher-routes-by-hook-type" in reports[0].dropped


def test_confidence_drop_detected(tmp_path):
    store = _seed_store(tmp_path, _OLD)
    dropped = {**_OLD, "confidence": 0.5}
    client = _make_fake_client([dropped])
    reports = diff([_MOD], tmp_path, store, agent=client)
    assert len(reports[0].confidence_drops) == 1
    assert reports[0].confidence_drops[0]["was"] == 0.9
    assert reports[0].confidence_drops[0]["now"] == 0.5


def test_skips_files_not_in_source_list(tmp_path):
    store = _seed_store(tmp_path, _OLD)
    client = _make_fake_client([])
    reports = diff(["some/random/file.py"], tmp_path, store, agent=client)
    assert reports == []


def test_caps_at_max_files(tmp_path):
    store = ConceptStore(tmp_path / "concepts.json")
    client = _make_fake_client([])
    files = [
        "hooks/dispatcher.py",
        "hooks/gates.py",
        "hooks/server.py",
        "hooks/server_memory.py",
    ]
    import concept_store.diff as _diff
    original_max = _diff._MAX_FILES_PER_INVOCATION
    _diff._MAX_FILES_PER_INVOCATION = 2
    try:
        reports = diff(files, tmp_path, store, agent=client)
        assert len(reports) <= 2
    finally:
        _diff._MAX_FILES_PER_INVOCATION = original_max


def test_format_drift_no_drift():
    r = DriftReport(module=_MOD)
    assert "no drift detected" in format_drift([r])


def test_format_drift_with_changes():
    r = DriftReport(
        module=_MOD,
        changed=[{"name": "foo", "field": "invariants", "was": ["old"], "now": ["new"]}],
        added=["new-concept"],
        dropped=["old-concept"],
        confidence_drops=[{"name": "foo", "was": 0.9, "now": 0.5}],
    )
    out = format_drift([r])
    assert "~ foo: invariants changed" in out
    assert "+ new concept: new-concept" in out
    assert "- dropped: old-concept" in out
    assert "↓ confidence drop: foo" in out
