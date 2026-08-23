"""Concept store and its MCP tools.

The property under test above all others is that the framework can read its own
concept store without another project's server. Before this, task-framework had
a store it could not open — a dependency acquired by omission.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from taskfw import mcp_server as m
from taskfw.concepts import ConceptStore

REAL_REPO = Path(__file__).parent.parent


def concept(name="a-concept", module="taskfw/store.py", **kw) -> dict:
    return {"name": name, "module": module,
            "description": "Does a thing, and promises to keep doing it.", **kw}


@pytest.fixture
def repo(tmp_path) -> Path:
    (tmp_path / "concept_store").mkdir()
    (tmp_path / "concept_store" / "concepts.json").write_text(
        json.dumps({"concepts": {}, "meta": {"repo": "fixture"}})
    )
    return tmp_path


class TestSelfContained:
    def test_taskfw_can_read_its_own_store(self):
        """The whole point — no external server involved."""
        store = ConceptStore(REAL_REPO)
        assert len(store.list()) >= 10
        assert "taskfw/lifecycle.py" in store.modules()

    def test_the_shipped_store_round_trips_through_the_tools(self):
        got = m.concept__get(str(REAL_REPO), "lifecycle-single-rule-implementation")
        assert got["found"] is True
        assert got["concept"]["module"] == "taskfw/lifecycle.py"
        assert got["concept"]["invariants"]


class TestShapeHandling:
    def test_a_list_shaped_store_is_rejected_loudly(self, tmp_path):
        """The failure that cost real time: a list breaks every reader.

        Rejecting at load with a message naming the problem beats an
        AttributeError surfacing three frames away.
        """
        (tmp_path / "concept_store").mkdir()
        (tmp_path / "concept_store" / "concepts.json").write_text(
            json.dumps({"concepts": [{"name": "x"}], "meta": {}})
        )
        with pytest.raises(ValueError, match="name-keyed map"):
            ConceptStore(tmp_path)

    def test_a_bare_map_without_the_wrapper_still_loads(self, tmp_path):
        """Detected by absence of the key, not by guessing at contents."""
        (tmp_path / "concept_store").mkdir()
        (tmp_path / "concept_store" / "concepts.json").write_text(
            json.dumps({"legacy-concept": concept(name="legacy-concept")})
        )
        assert ConceptStore(tmp_path).get("legacy-concept")

    def test_missing_store_is_empty_not_an_error(self, tmp_path):
        assert ConceptStore(tmp_path).list() == []

    def test_saved_file_keeps_the_canonical_shape(self, repo):
        ConceptStore(repo).upsert(concept())
        raw = json.loads((repo / "concept_store" / "concepts.json").read_text())
        assert isinstance(raw["concepts"], dict)
        assert raw["concepts"]["a-concept"]["name"] == "a-concept"

    def test_non_ascii_is_written_literally_not_escaped(self, repo):
        """Regression: json.dumps defaults to ensure_ascii=True, which
        re-escapes every em-dash and non-ASCII character in an UNTOUCHED
        concept to \\uXXXX on any single-entry write — a whole-file diff for a
        one-entry change. Caught cross-repo (task:756c14db) when this store's
        own writer mangled claude-hooks' concepts.json. Checking the raw bytes,
        not the round-tripped value, since json.loads silently accepts either
        encoding — only the file on disk shows the bug."""
        store = ConceptStore(repo)
        store.upsert(concept(description="Uses an em dash — like this."))
        raw_bytes = (repo / "concept_store" / "concepts.json").read_text()
        assert "—" in raw_bytes
        assert "\\u2014" not in raw_bytes


class TestUpsert:
    def test_creates_and_stamps_timestamps(self, repo):
        c = ConceptStore(repo).upsert(concept())
        assert c["created_at"] and c["last_validated"]
        assert c["confidence"] == 0.8

    def test_merges_rather_than_overwrites(self, repo):
        """Updating one field must not silently drop the others."""
        store = ConceptStore(repo)
        store.upsert(concept(invariants=["must stay true"], contracts=["f() -> g"]))
        store.upsert({"name": "a-concept", "module": "taskfw/store.py",
                      "description": "Revised description."})
        got = ConceptStore(repo).get("a-concept")
        assert got["description"] == "Revised description."
        assert got["invariants"] == ["must stay true"], "merge dropped invariants"
        assert got["contracts"] == ["f() -> g"]

    def test_a_payload_naming_only_some_list_entries_unions_not_replaces(self, repo):
        """A caller touching invariants at all must not drop the ones it didn't mention."""
        store = ConceptStore(repo)
        store.upsert(concept(invariants=["first", "second"], contracts=["f() -> g"]))
        store.upsert(concept(invariants=["third"]))
        got = ConceptStore(repo).get("a-concept")
        assert got["invariants"] == ["first", "second", "third"]
        assert got["contracts"] == ["f() -> g"], "untouched list field must survive too"

    def test_upserting_a_duplicate_list_entry_does_not_repeat_it(self, repo):
        store = ConceptStore(repo)
        store.upsert(concept(invariants=["first"]))
        store.upsert(concept(invariants=["first", "second"]))
        assert ConceptStore(repo).get("a-concept")["invariants"] == ["first", "second"]

    def test_an_explicit_empty_list_still_clears(self, repo):
        """Deletion stays possible — it just has to be deliberate."""
        store = ConceptStore(repo)
        store.upsert(concept(invariants=["old"]))
        store.upsert(concept(invariants=[]))
        assert ConceptStore(repo).get("a-concept")["invariants"] == []

    def test_created_at_survives_an_update(self, repo):
        store = ConceptStore(repo)
        first = store.upsert(concept())
        second = store.upsert(concept(description="Changed, but still a description."))
        assert second["created_at"] == first["created_at"]

    def test_required_fields_are_enforced(self, repo):
        with pytest.raises(ValueError, match="missing required"):
            ConceptStore(repo).upsert({"name": "x", "module": "y"})


class TestQueries:
    @pytest.fixture
    def populated(self, repo):
        store = ConceptStore(repo)
        store.upsert(concept(name="alpha", module="a.py", invariants=["never blocks"]))
        store.upsert(concept(name="beta", module="b.py",
                             description="Handles migrations additively and safely."))
        return repo

    def test_list_filters_by_module(self, populated):
        assert [c["name"] for c in ConceptStore(populated).list("a.py")] == ["alpha"]

    def test_modules_are_deduped_and_sorted(self, populated):
        assert ConceptStore(populated).modules() == ["a.py", "b.py"]

    def test_search_covers_description_and_invariants(self, populated):
        store = ConceptStore(populated)
        assert [c["name"] for c in store.search("migrations")] == ["beta"]
        assert [c["name"] for c in store.search("never blocks")] == ["alpha"]

    def test_empty_search_returns_nothing_rather_than_everything(self, populated):
        assert ConceptStore(populated).search("  ") == []

    def test_uncovered_reports_modules_with_no_concept(self, populated):
        assert ConceptStore(populated).uncovered(["a.py", "c.py"]) == ["c.py"]


class TestDelete:
    def test_deletes_and_reports(self, repo):
        store = ConceptStore(repo)
        store.upsert(concept())
        assert store.delete("a-concept") is True
        assert ConceptStore(repo).get("a-concept") is None

    def test_deleting_a_missing_concept_is_false_not_an_error(self, repo):
        assert ConceptStore(repo).delete("nope") is False


class TestTools:
    def test_get_missing_reports_found_false(self, repo):
        assert m.concept__get(str(repo), "nope")["found"] is False

    def test_get_found_nests_the_concept_rather_than_flattening_it(self, repo):
        """{"found": bool, "concept": {...}} on both branches — not the fields
        flattened onto the response. Matches claude-hooks' own concept__get,
        which several of its skills already depend on by bare name
        (task:756c14db) — a caller checking result["found"] must get the same
        answer regardless of which server's tool actually answered the call."""
        ConceptStore(repo).upsert(concept())
        got = m.concept__get(str(repo), "a-concept")
        assert got["found"] is True
        assert got["concept"]["name"] == "a-concept"
        assert "name" not in got, "fields must not also be flattened onto the response"

    def test_upsert_rejects_incomplete_concepts_without_raising(self, repo):
        assert "error" in m.concept__upsert(str(repo), {"name": "x"})

    def test_tools_round_trip(self, repo):
        r = str(repo)
        assert m.concept__upsert(r, concept())["ok"]
        assert m.concept__modules(r)["modules"] == ["taskfw/store.py"]
        assert len(m.concept__search(r, "promises")["concepts"]) == 1
        assert m.concept__uncovered(r, ["taskfw/store.py", "other.py"])["uncovered"] == ["other.py"]
        assert m.concept__delete(r, "a-concept")["deleted"] is True

    @pytest.mark.anyio
    async def test_every_concept_tool_is_registered(self):
        names = {t.name for t in await m.mcp.list_tools()}
        assert {"concept__list", "concept__get", "concept__upsert", "concept__delete",
                "concept__modules", "concept__search", "concept__uncovered"} <= names


@pytest.fixture
def anyio_backend():
    return "asyncio"
