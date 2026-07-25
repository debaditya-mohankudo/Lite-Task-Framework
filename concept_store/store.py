"""ConceptStore — JSON-backed store for architectural concepts extracted from claude-hooks."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_DEFAULT_FILENAME = "concepts.json"


class ConceptStore:
    """Stores architectural concepts as a JSON file keyed by concept name.

    Each concept:
        name        — unique slug (e.g. "dispatcher-routes-by-hook-type")
        module      — source file (e.g. "hooks/dispatcher.py")
        description — what this module/concept does architecturally
        invariants  — list[str] of constraints that must always hold
        contracts   — list[str] of promises to callers
        confidence  — float 0.0–1.0
        evidence    — list[str] of "file:line" references
        last_validated — ISO timestamp
        created_at     — ISO timestamp
    """

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._data: dict[str, dict] = {}
        # Store-level metadata (commit the concepts were extracted at, extraction
        # timestamp) -- same top-level {"meta": ..., "concepts": ...} wrapper as the
        # ACME_Cert_Life_Cycle / bee-bug-hunter concept stores.
        self._meta: dict = {"commit": "", "extracted_at": "", "note": ""}
        if self._path.exists():
            text = self._path.read_text(encoding="utf-8").strip()
            raw = json.loads(text) if text else {}
            if "concepts" in raw:
                self._data = raw["concepts"]
                self._meta.update(raw.get("meta", {}))
            else:
                # Legacy flat layout ({name: concept}) from before the meta wrapper.
                self._data = raw

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def upsert(self, concept: dict) -> None:
        name = concept["name"]
        now = datetime.now(timezone.utc).isoformat()
        existing = self._data.get(name, {})
        self._data[name] = {
            "name":           name,
            "module":         concept.get("module", ""),
            "description":    concept.get("description", ""),
            "invariants":     concept.get("invariants", []),
            "contracts":      concept.get("contracts", []),
            "confidence":     concept.get("confidence", 0.0),
            "evidence":       concept.get("evidence", []),
            "last_validated": now,
            "created_at":     existing.get("created_at", now),
        }
        self.save()

    def delete(self, name: str) -> None:
        self._data.pop(name, None)
        self.save()

    def save(self) -> None:
        payload = {"meta": self._meta, "concepts": self._data}
        self._path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )

    def set_meta(self, **fields) -> None:
        """Merge store-level metadata (e.g. commit=<sha>, extracted_at=<iso>) and persist."""
        self._meta.update(fields)
        self.save()

    @property
    def meta(self) -> dict:
        return dict(self._meta)

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get(self, name: str) -> Optional[dict]:
        return self._data.get(name)

    def list(self, module: Optional[str] = None) -> list[dict]:
        concepts = list(self._data.values())
        if module is not None:
            concepts = [c for c in concepts if c.get("module") == module]
        return concepts

    def modules(self) -> list[str]:
        return sorted({c.get("module", "") for c in self._data.values() if c.get("module")})

    def search(self, query: str) -> list[dict]:
        """Keyword search across name/description/invariants/contracts.

        Case-insensitive substring match. Ranks by number of matching fields
        (name/description count once each; invariants/contracts count once
        per matching list entry), most matches first, ties broken by name.
        Closes the gap between get() (needs the exact name) and list()
        (needs the exact module) — free-text lookup across the whole store.
        """
        needle = query.lower().strip()
        if not needle:
            return []

        scored: list[tuple[int, dict]] = []
        for concept in self._data.values():
            score = 0
            if needle in concept.get("name", "").lower():
                score += 1
            if needle in concept.get("description", "").lower():
                score += 1
            for inv in concept.get("invariants", []) or []:
                if needle in str(inv).lower():
                    score += 1
            for con in concept.get("contracts", []) or []:
                if needle in str(con).lower():
                    score += 1
            if score:
                scored.append((score, concept))

        scored.sort(key=lambda pair: (-pair[0], pair[1].get("name", "")))
        return [c for _, c in scored]

    def __len__(self) -> int:
        return len(self._data)
