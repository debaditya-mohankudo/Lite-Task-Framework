"""Unit tests for concept_store/extractor.py using a fake ClaudeCLI agent
(task:a91133b8 — no anthropic SDK / API key involved)."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from concept_store.extractor import extract
from concept_store.store import ConceptStore

_FAKE_CONCEPTS = [
    {
        "name": "dispatcher-routes-by-hook-type",
        "module": "hooks/dispatcher.py",
        "description": "Routes hook events to handler nodes based on event_type.",
        "invariants": ["all hooks must return HookResult"],
        "contracts": ["returns dict or None"],
        "confidence": 0.9,
        "evidence": ["hooks/dispatcher.py:42"],
    },
    {
        "name": "gates-prereq-chain",
        "module": "hooks/gates.py",
        "description": "Chains prerequisite verifiers before allowing tool execution.",
        "invariants": ["gate failures block tool execution"],
        "contracts": ["returns GateResult with allow bool"],
        "confidence": 0.85,
        "evidence": ["hooks/gates.py:80"],
    },
]


def _make_fake_agent(response_text: str) -> MagicMock:
    agent = MagicMock()
    agent.complete.return_value = response_text
    return agent


def test_extract_upserts_all_concepts(tmp_path):
    store = ConceptStore(tmp_path / "concepts.json")
    agent = _make_fake_agent(json.dumps(_FAKE_CONCEPTS))
    concepts = extract(tmp_path, store, agent=agent)
    assert len(concepts) == 2
    assert store.get("dispatcher-routes-by-hook-type") is not None
    assert store.get("gates-prereq-chain") is not None


def test_extract_calls_claude_once(tmp_path):
    store = ConceptStore(tmp_path / "concepts.json")
    agent = _make_fake_agent(json.dumps(_FAKE_CONCEPTS))
    extract(tmp_path, store, agent=agent)
    assert agent.complete.call_count == 1


def test_extract_raises_on_bad_json(tmp_path):
    store = ConceptStore(tmp_path / "concepts.json")
    agent = _make_fake_agent("not json at all")
    with pytest.raises(ValueError, match="unparseable JSON"):
        extract(tmp_path, store, agent=agent)


def test_extract_raises_on_non_array(tmp_path):
    store = ConceptStore(tmp_path / "concepts.json")
    agent = _make_fake_agent(json.dumps({"not": "an array"}))
    with pytest.raises(ValueError, match="Expected JSON array"):
        extract(tmp_path, store, agent=agent)


def test_concepts_persisted_to_json(tmp_path):
    store = ConceptStore(tmp_path / "concepts.json")
    agent = _make_fake_agent(json.dumps(_FAKE_CONCEPTS))
    extract(tmp_path, store, agent=agent)
    store2 = ConceptStore(tmp_path / "concepts.json")
    assert len(store2.list()) == 2


def test_extract_strips_markdown_fences(tmp_path):
    """Observed live (task:a91133b8): `claude -p` wraps its JSON response
    in ```json fences despite _SYSTEM explicitly saying not to."""
    store = ConceptStore(tmp_path / "concepts.json")
    fenced = "```json\n" + json.dumps(_FAKE_CONCEPTS) + "\n```"
    agent = _make_fake_agent(fenced)
    concepts = extract(tmp_path, store, agent=agent)
    assert len(concepts) == 2
