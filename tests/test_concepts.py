"""Concept store tests — coverage and shape, checked rather than remembered.

Grooming left one question open: is "refresh after each task" discipline or
structure? Answered here as STRUCTURE, but as a test rather than by wiring the
store into the runtime. Coupling taskfw.lifecycle to a concept store it
otherwise knows nothing about would earn a part in the model it does not
deserve; a test costs nothing and cannot be forgotten.

So: adding a module with no concept fails the suite. That is the enforcement.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
STORE = ROOT / "concept_store" / "concepts.json"
PACKAGE = ROOT / "taskfw"
TESTS = ROOT / "tests"

#: Modules too small or too mechanical to carry an architectural concept.
TRIVIAL = {"__init__.py", "config.py", "conftest.py"}


@pytest.fixture(scope="module")
def store() -> dict:
    return json.loads(STORE.read_text())


def source_modules() -> list[str]:
    return sorted(
        str(p.relative_to(ROOT))
        for package in (PACKAGE, TESTS)
        for p in package.rglob("*.py")
        if p.name not in TRIVIAL and "__pycache__" not in p.parts
    )


class TestShape:
    def test_concepts_is_a_name_keyed_map(self, store):
        """NOT a list.

        The tooling does .values() on this, so a list breaks every reader with
        an unhelpful AttributeError. Worth pinning: the surrounding
        documentation describes the opposite shape, and following it produced
        a store no tool could read.
        """
        assert isinstance(store["concepts"], dict)

    def test_each_entry_is_keyed_by_its_own_name(self, store):
        for key, concept in store["concepts"].items():
            assert concept["name"] == key, f"{key} disagrees with its name field"

    def test_required_fields_are_present(self, store):
        for name, concept in store["concepts"].items():
            for field in ("module", "description", "contracts", "invariants",
                          "evidence", "confidence", "created_at", "last_validated"):
                assert field in concept, f"{name} is missing {field}"

    def test_meta_records_provenance(self, store):
        assert store["meta"]["repo"] == "task-framework"


class TestCoverage:
    def test_every_module_has_a_concept(self, store):
        """The structural answer to "refresh after each task".

        A new module with no concept fails here, so the store cannot silently
        fall behind the code.
        """
        covered = {c["module"] for c in store["concepts"].values()}
        missing = set(source_modules()) - covered
        assert not missing, f"modules with no concept: {sorted(missing)}"

    def test_no_concept_describes_a_deleted_module(self, store):
        known = set(source_modules())
        for name, concept in store["concepts"].items():
            assert concept["module"] in known, (
                f"{name} describes {concept['module']}, which no longer exists"
            )

    def test_evidence_points_at_real_files(self, store):
        for name, concept in store["concepts"].items():
            for ref in concept["evidence"]:
                path = ROOT / ref.split(":")[0]
                assert path.exists(), f"{name} cites missing file {ref}"


class TestSubstance:
    def test_every_concept_states_at_least_one_invariant(self, store):
        """A concept with no invariant is a summary, not architectural knowledge."""
        for name, concept in store["concepts"].items():
            assert concept["invariants"], f"{name} states no invariants"

    def test_descriptions_are_not_stubs(self, store):
        for name, concept in store["concepts"].items():
            assert len(concept["description"]) > 80, f"{name} has a stub description"


class TestModelDerivation:
    def test_the_model_is_derived_from_concepts_not_the_reverse(self, store):
        """Direction is one-way: code -> concepts -> model.

        Both encode architectural truth, so without a stated direction they
        drift. The meta note is where that direction is recorded.
        """
        assert "code -> concepts -> model" in store["meta"]["note"]
