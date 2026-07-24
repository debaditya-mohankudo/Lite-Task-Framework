"""Concept drift detection — re-extract changed files and diff against the store."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from concept_store.claude_cli import ClaudeCLI
from concept_store.store import ConceptStore
from concept_store.extractor import extract, _SOURCE_FILES

_CONFIDENCE_DROP_THRESHOLD = 0.2
_MAX_FILES_PER_INVOCATION = 3


@dataclass
class DriftReport:
    module: str
    changed: list[dict] = field(default_factory=list)   # [{name, field, was, now}]
    added: list[str] = field(default_factory=list)       # new concept names
    dropped: list[str] = field(default_factory=list)     # concept names no longer present
    confidence_drops: list[dict] = field(default_factory=list)  # [{name, was, now}]

    @property
    def has_drift(self) -> bool:
        return bool(self.changed or self.added or self.dropped or self.confidence_drops)


def _list_diff(old: list[str], new: list[str]) -> tuple[list[str], list[str]]:
    old_set, new_set = set(old), set(new)
    return sorted(new_set - old_set), sorted(old_set - new_set)


def _concept_field_drift(old: dict, new: dict) -> list[dict]:
    changes = []
    for f in ("description", "invariants", "contracts"):
        ov, nv = old.get(f), new.get(f)
        if ov != nv:
            changes.append({"name": old["name"], "field": f, "was": ov, "now": nv})
    return changes


def diff(
    changed_files: list[str],
    repo_root: Path,
    store: ConceptStore,
    agent: Optional[ClaudeCLI] = None,
) -> list[DriftReport]:
    """Re-extract concepts for changed_files and diff against the store.

    Caps at _MAX_FILES_PER_INVOCATION files; skips the rest with a note in the report.

    `agent` is forwarded to extract() as-is (including None) — extract()
    handles constructing a default ClaudeCLI per call, so diff() doesn't
    need its own default-construction logic (task:a91133b8).
    """
    repo_root = Path(repo_root)
    repo_rel = [
        f for f in changed_files
        if f in _SOURCE_FILES
    ][:_MAX_FILES_PER_INVOCATION]

    if not repo_rel:
        return []

    reports = []
    for rel_file in repo_rel:
        # Re-extract concepts for this single file into a temp store
        from concept_store.store import ConceptStore as _CS
        import tempfile, os
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
            tmp_json_path = Path(tf.name)
        try:
            tmp_store = _CS(tmp_json_path)
            # Override source file list to just this file
            import concept_store.extractor as _ext
            original = _ext._SOURCE_FILES
            _ext._SOURCE_FILES = [rel_file]
            try:
                extract(repo_root, tmp_store, agent=agent)
            finally:
                _ext._SOURCE_FILES = original

            new_concepts = {c["name"]: c for c in tmp_store.list(module=rel_file)}
            old_concepts = {c["name"]: c for c in store.list(module=rel_file)}

            report = DriftReport(module=rel_file)

            added_names, dropped_names = _list_diff(list(old_concepts), list(new_concepts))
            report.added = added_names
            report.dropped = dropped_names

            for name in set(old_concepts) & set(new_concepts):
                old_c, new_c = old_concepts[name], new_concepts[name]
                changes = _concept_field_drift(old_c, new_c)
                report.changed.extend(changes)
                old_conf = old_c.get("confidence", 0.0)
                new_conf = new_c.get("confidence", 0.0)
                if (old_conf - new_conf) >= _CONFIDENCE_DROP_THRESHOLD:
                    report.confidence_drops.append({"name": name, "was": old_conf, "now": new_conf})

            reports.append(report)
        finally:
            tmp_json_path.unlink(missing_ok=True)

    return reports


def format_drift(reports: list[DriftReport]) -> str:
    if not reports:
        return ""
    lines = []
    for r in reports:
        if not r.has_drift:
            lines.append(f"[concept-drift] no drift detected in {r.module}")
            continue
        lines.append(f"[concept-drift] {r.module}")
        for c in r.changed:
            lines.append(f"  ~ {c['name']}: {c['field']} changed")
            lines.append(f"    was: {json.dumps(c['was'])}")
            lines.append(f"    now: {json.dumps(c['now'])}")
        for name in r.added:
            lines.append(f"  + new concept: {name}")
        for name in r.dropped:
            lines.append(f"  - dropped: {name}")
        for cd in r.confidence_drops:
            lines.append(f"  ↓ confidence drop: {cd['name']} {cd['was']:.2f} → {cd['now']:.2f}")
    return "\n".join(lines)
