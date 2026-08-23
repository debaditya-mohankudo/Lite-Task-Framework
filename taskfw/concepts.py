"""Concept store — architectural knowledge about a repo's modules.

A concept records what a module promises and what must stay true of it:
description, contracts, invariants, and evidence pointing at the code. Ground
truth is always the code; this is a durable summary so a future reader does not
have to re-derive it.

FORMAT, and it is deliberately not ours to invent. The on-disk shape matches
the established one so a store written by either implementation stays readable
by both:

    {"concepts": {"<slug>": {name, module, description, contracts,
                             invariants, evidence, related, confidence,
                             created_at, last_validated}},
     "meta": {...}}

`concepts` is a NAME-KEYED MAP, not a list. The single most common way to get
this wrong is calling .values() or .items() on the LOADED FILE rather than on
its "concepts" key — that iterates "concepts" and "meta" as though they were
concepts, and either matches nothing or raises KeyError on the first field
access. Every accessor here goes through self._concepts for exactly that
reason, and tests pin it.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from taskfw.log import get_logger

log = get_logger(__name__)

STORE_RELPATH = Path("concept_store") / "concepts.json"

#: Fields every concept carries. Anything else is preserved but not required.
REQUIRED = ("name", "module", "description")

#: List-valued fields that upsert merges (union) rather than replaces outright.
LIST_FIELDS = ("contracts", "invariants", "evidence", "related")

#: `related` is a pointer list (other concept slugs), not an evidence trail —
#: it stays useful only if short. Capped, not truncated: a caller that would
#: overflow it gets a ValueError, never a silent drop (CLAUDE.md: an omission
#: must never be indistinguishable from an absence).
RELATED_MAX = 3


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ConceptStore:
    """JSON-backed concept store for one repository."""

    repo: Path
    _concepts: dict[str, dict] = field(default_factory=dict, init=False)
    _meta: dict = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        self.repo = Path(self.repo).expanduser()
        self.load()

    # -- persistence --------------------------------------------------------

    @property
    def path(self) -> Path:
        return self.repo / STORE_RELPATH

    def load(self) -> "ConceptStore":
        if not self.path.exists():
            log.debug("concept store: no file at %s", self.path)
            self._concepts, self._meta = {}, {}
            return self
        raw = json.loads(self.path.read_text() or "{}")
        # A store written before the wrapper existed is the bare map itself.
        # Detected by absence of the key, not by guessing at the contents.
        self._concepts = raw["concepts"] if "concepts" in raw else raw
        self._meta = raw.get("meta", {})
        if not isinstance(self._concepts, dict):
            raise ValueError(
                f"{self.path}: 'concepts' must be a name-keyed map, got "
                f"{type(self._concepts).__name__}. A list breaks every reader."
            )
        log.debug("concept store: loaded %d concepts from %s", len(self._concepts), self.path)
        return self

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._meta["updated_at"] = _utcnow()
        payload = {"concepts": self._concepts, "meta": self._meta}
        # ensure_ascii=False: without it, every em-dash and non-ASCII character
        # in an untouched concept gets re-escaped to \uXXXX on any write,
        # producing a diff across the whole file for a change to one entry.
        # Matches claude-hooks' own concept_store/store.py convention — writing
        # this file with either implementation must not visibly disagree.
        self.path.write_text(
            json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        )
        log.info("concept store: wrote %d concepts to %s", len(self._concepts), self.path)

    # -- reads --------------------------------------------------------------

    def list(self, module: str = "") -> list[dict]:
        """Every concept, optionally filtered to one module."""
        found = [c for c in self._concepts.values() if not module or c.get("module") == module]
        return sorted(found, key=lambda c: c.get("name", ""))

    def get(self, name: str) -> dict | None:
        return self._concepts.get(name)

    def modules(self) -> list[str]:
        """Every module that has at least one concept."""
        return sorted({c["module"] for c in self._concepts.values() if c.get("module")})

    def search(self, query: str) -> list[dict]:
        """Substring match over name, module, description, contracts, invariants."""
        q = query.lower().strip()
        if not q:
            return []
        hits = []
        for concept in self._concepts.values():
            haystack = " ".join([
                concept.get("name", ""), concept.get("module", ""),
                concept.get("description", ""),
                " ".join(concept.get("contracts", []) or []),
                " ".join(concept.get("invariants", []) or []),
            ]).lower()
            if q in haystack:
                hits.append(concept)
        return sorted(hits, key=lambda c: c.get("name", ""))

    def uncovered(self, modules: list[str]) -> list[str]:
        """Modules with no concept — what a coverage check reports."""
        return sorted(set(modules) - set(self.modules()))

    # -- writes -------------------------------------------------------------

    def upsert(self, concept: dict) -> dict:
        """Insert or MERGE a concept.

        Merges rather than overwrites: a caller updating one field should not
        silently drop invariants it did not mention. This applies within a
        list field too — a payload naming only some entries of `contracts`,
        `invariants`, or `evidence` unions them with what is already stored
        rather than replacing the list outright. Passing an explicit empty
        list still clears that field, so deletion stays possible — it just
        has to be deliberate. Every other field replaces if present, same as
        before.
        """
        missing = [f for f in REQUIRED if not concept.get(f)]
        if missing:
            raise ValueError(f"concept is missing required fields: {missing}")

        name = concept["name"]
        existing = self._concepts.get(name, {})
        merged = {**existing, **concept}
        for lf in LIST_FIELDS:
            if lf in concept:
                incoming = concept[lf] or []
                if incoming:
                    prior = existing.get(lf, []) or []
                    merged[lf] = prior + [item for item in incoming if item not in prior]
                else:
                    merged[lf] = []
            else:
                merged[lf] = existing.get(lf, [])
        if len(merged.get("related", [])) > RELATED_MAX:
            raise ValueError(
                f"concept {name!r}: related would grow to "
                f"{len(merged['related'])} entries, max is {RELATED_MAX}"
            )
        merged.setdefault("confidence", 0.8)
        merged.setdefault("created_at", existing.get("created_at") or _utcnow())
        merged["last_validated"] = _utcnow()

        self._concepts[name] = merged
        self.save()
        log.info("concept %s: %s module=%s", name,
                 "updated" if existing else "created", merged.get("module"))
        return merged

    def delete(self, name: str) -> bool:
        if name not in self._concepts:
            log.info("concept %s: not found, nothing deleted", name)
            return False
        del self._concepts[name]
        self.save()
        log.info("concept %s: deleted", name)
        return True
