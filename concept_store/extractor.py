"""One-shot architectural concept extraction from the claude-hooks codebase."""
from __future__ import annotations

import json
from pathlib import Path

from concept_store.claude_cli import ClaudeCLI
from concept_store.store import ConceptStore

# "sonnet" (alias, tracks the latest Sonnet) rather than a pinned full model
# name — matches SeniorDevAgent's ClaudeCLILLM default, and avoids going
# stale as models rotate (task:a91133b8; was "claude-sonnet-4-6", an
# anthropic-SDK-style model id left over from the pre-CLI implementation).
_MODEL = "sonnet"

_SOURCE_FILES = [
    "hooks/dispatcher.py",
    "hooks/gates.py",
    "hooks/server.py",
    "hooks/server_memory.py",
    "hooks/ui/routes.py",
    "hooks/ui/deps.py",
    "src/dispatcher.py",
    "src/tools/tasks.py",
    "src/tools/memory.py",
    "src/tools/hooks.py",
    "src/tools/code_rag.py",
    "src/tools/diff_rag.py",
    "src/tools/rag_core.py",
    "src/db/schema.py",
    "src/config.py",
    "src/logger.py",
    "mcp_server.py",
]

_SYSTEM = (
    "You are an expert software architect performing a one-shot architectural analysis. "
    "Respond with a JSON array only. No prose, no markdown fences."
)

_INSTRUCTIONS = """
Analyze the codebase above and extract architectural concepts.

For each logical unit (file or coherent subsystem), return one JSON object with:
  - name: unique kebab-case slug (e.g. "dispatcher-routes-by-hook-type")
  - module: source file path (e.g. "hooks/dispatcher.py")
  - description: what this module/concept does architecturally (1-3 sentences)
  - invariants: list of strings — constraints that must always hold
  - contracts: list of strings — what this module promises its callers
  - confidence: float 0.0–1.0 — how certain you are about this concept
  - evidence: list of "file:line" strings referencing where you saw this

Return a single top-level JSON array of these objects. Nothing else.
"""


def _strip_json_response(raw: str) -> str:
    """Strip markdown code fences the model wraps its JSON in, despite the
    system prompt explicitly asking it not to (observed live via task:
    a91133b8's manual drift-detection check — `claude -p` returned
    ```json\\n[...]\\n``` even with "No prose, no markdown fences" in
    _SYSTEM). Same fence-stripping SeniorDevAgent's loop/pass2.py::
    _parse_json uses, ported (not imported — separate repo) since this
    module's payload is a top-level JSON array, not that function's
    object-only fallback shape."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return raw


def _read_sources(repo_root: Path) -> str:
    parts = []
    for rel in _SOURCE_FILES:
        path = repo_root / rel
        if not path.exists():
            continue
        content = path.read_text(encoding="utf-8")
        parts.append(f"### {rel}\n{content}")
    return "\n\n".join(parts)


def extract(repo_root: Path, store: ConceptStore, agent: ClaudeCLI | None = None) -> list[dict]:
    """Read all source files, call Claude once, parse concepts, upsert into store.

    Uses ClaudeCLI (`claude -p` subprocess, authenticated via the existing
    Claude Code login) rather than the anthropic SDK — no
    ANTHROPIC_API_KEY required (task:a91133b8). ClaudeCLI is a ported
    subset of SeniorDevAgent's ClaudeCLILLM (structured JSON output,
    is_error checking, timeout/heartbeat), not the simpler BareClaudeAgent
    already in this repo — see concept_store/claude_cli.py's docstring for
    why."""
    if agent is None:
        agent = ClaudeCLI(model=_MODEL)

    source = _read_sources(repo_root)
    user_message = f"{source}\n\n{_INSTRUCTIONS}"

    raw = _strip_json_response(agent.complete(_SYSTEM, user_message))
    try:
        concepts = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Claude returned unparseable JSON: {raw[:500]}") from exc

    if not isinstance(concepts, list):
        raise ValueError(f"Expected JSON array, got {type(concepts).__name__}: {raw[:200]}")

    for concept in concepts:
        store.upsert(concept)

    return concepts
