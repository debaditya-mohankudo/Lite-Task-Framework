"""Unit tests for ConceptStore."""
import json
import pytest
from concept_store.store import ConceptStore

_CONCEPT = {
    "name": "dispatcher-routes-by-hook-type",
    "module": "hooks/dispatcher.py",
    "description": "Routes hook events to handler nodes based on event_type.",
    "invariants": ["all hooks must return HookResult", "unknown events route to noop"],
    "contracts": ["returns dict with hookSpecificOutput or None"],
    "confidence": 0.9,
    "evidence": ["hooks/dispatcher.py:42"],
}


def test_upsert_and_get(tmp_path):
    store = ConceptStore(tmp_path / "concepts.json")
    store.upsert(_CONCEPT)
    result = store.get("dispatcher-routes-by-hook-type")
    assert result["description"] == _CONCEPT["description"]
    assert result["invariants"] == _CONCEPT["invariants"]
    assert result["confidence"] == 0.9


def test_upsert_replaces(tmp_path):
    store = ConceptStore(tmp_path / "concepts.json")
    store.upsert(_CONCEPT)
    updated = {**_CONCEPT, "confidence": 0.5, "description": "updated"}
    store.upsert(updated)
    result = store.get("dispatcher-routes-by-hook-type")
    assert result["confidence"] == 0.5
    assert result["description"] == "updated"


def test_upsert_preserves_created_at(tmp_path):
    store = ConceptStore(tmp_path / "concepts.json")
    store.upsert(_CONCEPT)
    created_at = store.get("dispatcher-routes-by-hook-type")["created_at"]
    store.upsert({**_CONCEPT, "description": "changed"})
    assert store.get("dispatcher-routes-by-hook-type")["created_at"] == created_at


def test_delete(tmp_path):
    store = ConceptStore(tmp_path / "concepts.json")
    store.upsert(_CONCEPT)
    store.delete("dispatcher-routes-by-hook-type")
    assert store.get("dispatcher-routes-by-hook-type") is None
    assert len(store) == 0


def test_list_all(tmp_path):
    store = ConceptStore(tmp_path / "concepts.json")
    store.upsert(_CONCEPT)
    store.upsert({**_CONCEPT, "name": "gates-prereq-chain", "module": "hooks/gates.py"})
    assert len(store.list()) == 2


def test_list_filtered_by_module(tmp_path):
    store = ConceptStore(tmp_path / "concepts.json")
    store.upsert(_CONCEPT)
    store.upsert({**_CONCEPT, "name": "gates-prereq-chain", "module": "hooks/gates.py"})
    results = store.list(module="hooks/gates.py")
    assert len(results) == 1
    assert results[0]["name"] == "gates-prereq-chain"


def test_modules(tmp_path):
    store = ConceptStore(tmp_path / "concepts.json")
    store.upsert(_CONCEPT)
    store.upsert({**_CONCEPT, "name": "gates-prereq-chain", "module": "hooks/gates.py"})
    assert store.modules() == ["hooks/dispatcher.py", "hooks/gates.py"]


def test_persistence(tmp_path):
    path = tmp_path / "concepts.json"
    store = ConceptStore(path)
    store.upsert(_CONCEPT)
    # reload from disk
    store2 = ConceptStore(path)
    assert store2.get("dispatcher-routes-by-hook-type") is not None
    assert store2.get("dispatcher-routes-by-hook-type")["confidence"] == 0.9


def test_empty_store_get_returns_none(tmp_path):
    store = ConceptStore(tmp_path / "concepts.json")
    assert store.get("nonexistent") is None


def test_json_file_written(tmp_path):
    path = tmp_path / "concepts.json"
    store = ConceptStore(path)
    store.upsert(_CONCEPT)
    data = json.loads(path.read_text())
    assert "dispatcher-routes-by-hook-type" in data["concepts"]


def test_search_matches_name(tmp_path):
    store = ConceptStore(tmp_path / "concepts.json")
    store.upsert(_CONCEPT)
    store.upsert({**_CONCEPT, "name": "gates-prereq-chain", "module": "hooks/gates.py"})
    results = store.search("dispatcher")
    assert len(results) == 1
    assert results[0]["name"] == "dispatcher-routes-by-hook-type"


def test_search_matches_description_invariants_contracts(tmp_path):
    store = ConceptStore(tmp_path / "concepts.json")
    store.upsert(_CONCEPT)
    assert len(store.search("event_type")) == 1  # description
    assert len(store.search("noop")) == 1  # invariants
    assert len(store.search("hookSpecificOutput")) == 1  # contracts


def test_search_is_case_insensitive(tmp_path):
    store = ConceptStore(tmp_path / "concepts.json")
    store.upsert(_CONCEPT)
    assert len(store.search("DISPATCHER")) == 1


def test_search_no_match_returns_empty(tmp_path):
    store = ConceptStore(tmp_path / "concepts.json")
    store.upsert(_CONCEPT)
    assert store.search("nonexistent-topic") == []


def test_search_empty_query_returns_empty(tmp_path):
    store = ConceptStore(tmp_path / "concepts.json")
    store.upsert(_CONCEPT)
    assert store.search("") == []
    assert store.search("   ") == []


def test_search_ranks_more_matches_first(tmp_path):
    store = ConceptStore(tmp_path / "concepts.json")
    # "hook" appears in name + description + invariants + contracts (4 fields)
    store.upsert(_CONCEPT)
    # "hooks" only appears in module, not searched — add a weaker match concept
    store.upsert({
        **_CONCEPT,
        "name": "gates-prereq-chain",
        "module": "hooks/gates.py",
        "description": "unrelated hook thing",
        "invariants": [],
        "contracts": [],
    })
    results = store.search("hook")
    assert results[0]["name"] == "dispatcher-routes-by-hook-type"
