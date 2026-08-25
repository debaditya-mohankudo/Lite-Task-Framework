"""Ontology tests — evidence checked against real code, not remembered.

ontology/task-domain.json is prose about the domain, and prose rots
silently: a term cites a symbol, the symbol gets renamed or the file moves,
and nothing notices until a human happens to re-read the file. Unlike
concept_store/concepts.json (enforced 1:1 against the file tree by
tests/test_concepts.py) and models/*.sysml (checked against real source by
tests/test_models.py), the ontology's own meta note says "nothing currently
tests it against the code, so treat it as a map that can drift, not a claim
that's checked" -- this file is what makes that statement false.

Scope is deliberately shallow: does the cited file exist, and does the cited
symbol appear in it (a substring check, not an AST lookup). That is enough
to catch the failure mode that actually happens -- a rename or a delete --
without taking on the cost of a real symbol resolver for a hand-written
prose file.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
ONTOLOGY = ROOT / "ontology" / "task-domain.json"

#: Objects a relation may point at that are not themselves ontology terms.
#: SourceModule is a placeholder for "some file in the repo", not a domain
#: term with its own definition -- see Concept's "describes" relation.
NON_TERM_OBJECTS = {"SourceModule"}


@pytest.fixture(scope="module")
def ontology() -> dict:
    return json.loads(ONTOLOGY.read_text())


def parse_evidence(evidence: str) -> list[tuple[str, str]]:
    """Split an evidence string into (file, symbol) pairs.

    Format is "path:symbol[, symbol2, ...][, path2:symbol3, ...]" -- a
    citation without its own ':' inherits the file of the citation before
    it, so "a.py:X, Y" means X and Y are both in a.py.
    """
    pairs = []
    current_file = None
    for chunk in evidence.split(","):
        chunk = chunk.strip()
        if ":" in chunk:
            current_file, symbol = chunk.split(":", 1)
        else:
            symbol = chunk
        assert current_file, f"evidence citation {chunk!r} has no file (first chunk must have one)"
        pairs.append((current_file, symbol))
    return pairs


class TestShape:
    def test_every_term_has_bounded_context_definition_and_evidence(self, ontology):
        for name, term in ontology["terms"].items():
            for field in ("bounded_context", "definition", "evidence"):
                assert field in term, f"{name} is missing {field}"

    def test_every_term_bounded_context_is_declared(self, ontology):
        contexts = set(ontology["bounded_contexts"])
        for name, term in ontology["terms"].items():
            assert term["bounded_context"] in contexts, (
                f"{name} claims undeclared bounded context {term['bounded_context']!r}"
            )


class TestEvidence:
    def test_evidence_files_exist(self, ontology):
        for name, term in ontology["terms"].items():
            for path, _symbol in parse_evidence(term["evidence"]):
                assert (ROOT / path).exists(), f"{name} cites missing file {path}"

    def test_evidence_symbols_appear_in_their_file(self, ontology):
        for name, term in ontology["terms"].items():
            for path, symbol in parse_evidence(term["evidence"]):
                # A symbol like "Task.grooming" cites a field access, not a
                # bare identifier -- the base name is what's grep-able.
                needle = symbol.split(".")[0]
                text = (ROOT / path).read_text()
                assert needle in text, (
                    f"{name} cites {symbol!r} in {path}, which no longer contains it"
                )


class TestRelations:
    def test_every_relation_subject_is_a_term(self, ontology):
        terms = set(ontology["terms"])
        for rel in ontology["relations"]:
            assert rel["subject"] in terms, f"relation subject {rel['subject']!r} is not a defined term"

    def test_every_relation_object_is_a_term_or_declared_exception(self, ontology):
        terms = set(ontology["terms"])
        for rel in ontology["relations"]:
            assert rel["object"] in terms or rel["object"] in NON_TERM_OBJECTS, (
                f"relation object {rel['object']!r} is neither a defined term nor a declared exception"
            )

    def test_every_relation_has_a_note(self, ontology):
        """A relation with no note is a type triple, not domain knowledge."""
        for rel in ontology["relations"]:
            assert rel.get("note"), f"{rel['subject']} {rel['predicate']} {rel['object']} has no note"
