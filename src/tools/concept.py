"""concept__* MCP tools (task:2813ece5) — thin wrappers around
concept_store/store.py's ConceptStore (JSON format), for Claude to read/
write architectural concepts inline during a session in a NON-Java repo.

Java/Spring repos are owned end-to-end by SeniorDevAgent's own Path 1/2/3
pipeline (a batch process) and use a SQLite-format concepts.db — explicitly
out of scope here. This is for the interactive, in-session read/write case
against a target repo's concept_store/concepts.json.

`repo` is REQUIRED on every tool (unlike code_rag.py's _resolve_repo, which
defaults to claude-hooks itself) — there is no sensible default target repo
for a tool whose whole purpose is operating on repos other than this one.
"""
from __future__ import annotations

from pathlib import Path

from concept_store.store import ConceptStore


def _resolve_repo(repo: str) -> Path:
    """repo -> validated Path. Mirrors code_rag.py's _resolve_repo, minus
    the default-to-claude-hooks fallback (see module docstring)."""
    if not repo:
        raise ValueError("repo is required")
    p = Path(repo).expanduser()
    if not p.is_dir():
        raise ValueError(f"Repo not found: {repo}")
    return p


def _store_for(repo: str) -> ConceptStore:
    return ConceptStore(_resolve_repo(repo) / "concept_store" / "concepts.json")


def handle_get(repo: str, name: str) -> dict:
    """Get one concept by name. {"found": False} if it doesn't exist."""
    try:
        store = _store_for(repo)
    except ValueError as exc:
        return {"error": str(exc)}
    concept = store.get(name)
    if concept is None:
        return {"found": False}
    return {"found": True, "concept": concept}


def handle_list(repo: str, module: str = "") -> dict:
    """All concepts, optionally filtered to one module."""
    try:
        store = _store_for(repo)
    except ValueError as exc:
        return {"error": str(exc)}
    concepts = store.list(module=module or None)
    return {"concepts": concepts}


def handle_upsert(repo: str, concept: dict) -> dict:
    """Insert or update a concept. Requires concept["name"]. Creates
    concept_store/ (and concepts.json) if this repo has neither yet —
    ConceptStore.__init__ already tolerates a missing FILE, but save()
    has no mkdir, so a missing DIRECTORY would otherwise crash on first
    write; this is the one place that gap needs covering."""
    if not concept.get("name"):
        return {"error": "concept['name'] is required"}
    try:
        repo_path = _resolve_repo(repo)
    except ValueError as exc:
        return {"error": str(exc)}
    store_dir = repo_path / "concept_store"
    store_dir.mkdir(parents=True, exist_ok=True)
    store = ConceptStore(store_dir / "concepts.json")
    store.upsert(concept)
    return {"ok": True, "name": concept["name"]}


def handle_delete(repo: str, name: str) -> dict:
    """Delete a concept by name. Not an error if it didn't exist."""
    try:
        store = _store_for(repo)
    except ValueError as exc:
        return {"error": str(exc)}
    existed = store.get(name) is not None
    store.delete(name)
    return {"ok": True, "deleted": existed}


def handle_modules(repo: str) -> dict:
    """Distinct module values across all stored concepts, sorted."""
    try:
        store = _store_for(repo)
    except ValueError as exc:
        return {"error": str(exc)}
    return {"modules": store.modules()}
