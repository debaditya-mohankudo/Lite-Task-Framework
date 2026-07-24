"""Tests for src/tools/concept.py — concept__* MCP tools wrapping the
JSON-format ConceptStore for a non-Java target repo (task:2813ece5)."""
import json

import pytest

from src.tools.concept import (
    handle_delete,
    handle_get,
    handle_list,
    handle_modules,
    handle_upsert,
)


@pytest.fixture
def repo(tmp_path):
    """A bare repo dir with no concept_store/ at all — upsert must create it."""
    return str(tmp_path)


@pytest.fixture
def repo_with_store(tmp_path):
    store_dir = tmp_path / "concept_store"
    store_dir.mkdir()
    (store_dir / "concepts.json").write_text(json.dumps({"meta": {}, "concepts": {}}))
    return str(tmp_path)


def _concept(name="foo-bar", module="foo.py", **overrides):
    base = {
        "name": name,
        "module": module,
        "description": "does foo things",
        "invariants": ["never does bar"],
        "contracts": ["returns a Foo"],
        "confidence": 0.7,
        "evidence": ["foo.py:1"],
    }
    base.update(overrides)
    return base


class TestResolveRepo:
    def test_missing_repo_arg_is_error(self):
        assert "error" in handle_get("", "foo")

    def test_nonexistent_repo_path_is_error(self):
        assert "error" in handle_get("/no/such/path/at/all", "foo")


class TestUpsertGet:
    def test_upsert_creates_concept_store_dir_when_absent(self, repo):
        result = handle_upsert(repo, _concept())
        assert result == {"ok": True, "name": "foo-bar"}
        got = handle_get(repo, "foo-bar")
        assert got["found"] is True
        assert got["concept"]["name"] == "foo-bar"

    def test_upsert_missing_name_is_error(self, repo):
        result = handle_upsert(repo, {"module": "foo.py"})
        assert "error" in result

    def test_get_missing_concept_not_found(self, repo_with_store):
        assert handle_get(repo_with_store, "nope") == {"found": False}

    def test_upsert_preserves_created_at_across_updates(self, repo):
        handle_upsert(repo, _concept(description="v1"))
        first = handle_get(repo, "foo-bar")["concept"]
        handle_upsert(repo, _concept(description="v2"))
        second = handle_get(repo, "foo-bar")["concept"]
        assert first["created_at"] == second["created_at"]
        assert second["description"] == "v2"
        assert second["last_validated"] >= first["last_validated"]


class TestList:
    def test_list_empty_store(self, repo_with_store):
        assert handle_list(repo_with_store) == {"concepts": []}

    def test_list_filters_by_module(self, repo):
        handle_upsert(repo, _concept(name="a", module="a.py"))
        handle_upsert(repo, _concept(name="b", module="b.py"))
        result = handle_list(repo, module="a.py")
        assert len(result["concepts"]) == 1
        assert result["concepts"][0]["name"] == "a"

    def test_list_no_filter_returns_all(self, repo):
        handle_upsert(repo, _concept(name="a", module="a.py"))
        handle_upsert(repo, _concept(name="b", module="b.py"))
        result = handle_list(repo)
        assert len(result["concepts"]) == 2


class TestDelete:
    def test_delete_existing(self, repo):
        handle_upsert(repo, _concept())
        result = handle_delete(repo, "foo-bar")
        assert result == {"ok": True, "deleted": True}
        assert handle_get(repo, "foo-bar") == {"found": False}

    def test_delete_missing_is_noop_not_error(self, repo_with_store):
        assert handle_delete(repo_with_store, "nope") == {"ok": True, "deleted": False}


class TestModules:
    def test_modules_sorted_distinct(self, repo):
        handle_upsert(repo, _concept(name="a", module="z.py"))
        handle_upsert(repo, _concept(name="b", module="a.py"))
        handle_upsert(repo, _concept(name="c", module="a.py"))
        assert handle_modules(repo) == {"modules": ["a.py", "z.py"]}
