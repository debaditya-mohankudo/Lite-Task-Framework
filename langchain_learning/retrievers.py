"""Public Protocol contracts for the retrieval and gate layers.

These interfaces are the joints in the memory injection pipe:
  prompt → tokenise → MemoryRetriever → LoadMemoriesNode → additionalSystemPrompt

Concrete implementations live in nodes/ and hooks/ and import from here.
This file must never import from nodes/ or hooks/ — the dependency arrow is:
  nodes/ → retrievers.py, never the reverse.

Design principle: Callable classes with minimal structural contracts (ACME POC Principle 13).
Protocols are structural — no inheritance required to satisfy them.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from src.logger import get_logger

_log = get_logger(__name__)

# GateContext is a dataclass defined in hooks/gates.py.
# We import the type only for the GatePolicy signature; the arrow goes:
#   hooks/gate_check.py → retrievers.py (for GatePolicy)
#   hooks/gates.py      → retrievers.py (only for GateContext type, which is fine)
# retrievers.py never calls into hooks/ — it only names the type.
from hooks.gates import GateContext


# ---------------------------------------------------------------------------
# MemoryRetriever
# ---------------------------------------------------------------------------

@runtime_checkable
class MemoryRetriever(Protocol):
    """Score and return relevant memories for a set of prompt tokens.

    Implementations:
      CombinationSignalRetriever — wraps score_memories() (nodes/_memory_scoring.py)
      NullMemoryRetriever        — returns [] (testing / disabled)
    """

    def retrieve(
        self,
        tokens: set[str],
        project_domain: str | None,
        top_n: int | None = None,
    ) -> list[dict]: ...


# ---------------------------------------------------------------------------
# ToolScorer
# ---------------------------------------------------------------------------

@runtime_checkable
class ToolScorer(Protocol):
    """Score and return relevant tool hints for a set of prompt keywords.

    Implementations:
      KeywordOverlapScorer — wraps ScoreToolsNode inline scoring (nodes/score_tools.py)
      NullToolScorer       — returns [] (testing / disabled)
    """

    def score(
        self,
        keywords: set[str],
        domains: set[str],
        top_n: int = 5,
    ) -> list[dict]: ...


# ---------------------------------------------------------------------------
# GatePolicy
# ---------------------------------------------------------------------------

@runtime_checkable
class GatePolicy(Protocol):
    """Check whether a tool call should be allowed or denied.

    Implementations:
      DefaultGatePolicy — wraps GATES dict dispatch (hooks/gates.py)
    """

    def check(self, tool_name: str, ctx: GateContext) -> tuple[bool, str]: ...


# ---------------------------------------------------------------------------
# Null implementations — safe defaults for testing and opt-out
# ---------------------------------------------------------------------------

class NullMemoryRetriever:
    """No-op retriever — always returns empty. Satisfies MemoryRetriever Protocol."""

    def retrieve(self, tokens: set[str], project_domain: str | None, top_n: int | None = None) -> list[dict]:
        return []


class NullToolScorer:
    """No-op scorer — always returns empty. Satisfies ToolScorer Protocol."""

    def score(self, keywords: set[str], domains: set[str], top_n: int = 5) -> list[dict]:
        return []


# ---------------------------------------------------------------------------
# Default implementations — live backends, used by nodes in production
# ---------------------------------------------------------------------------

class CombinationSignalRetriever:
    """Scores MEMORY.sqlite rows via domain weight + tag/body overlap + recency.

    Wraps score_memories() from nodes/_memory_scoring.py.
    Opens and closes its own SQLite connection per retrieve() call.
    Satisfies MemoryRetriever Protocol.
    """

    def retrieve(
        self,
        tokens: set[str],
        project_domain: str | None,
        top_n: int | None = None,
    ) -> list[dict]:
        import sqlite3
        from langchain_learning.config import config as _cfg
        from langchain_learning.nodes._memory_scoring import score_memories

        if not _cfg.memory_db.exists():
            _log.debug("[CombinationSignalRetriever] memory_db not found: %s", _cfg.memory_db)
            return []
        conn = sqlite3.connect(f"file:{_cfg.memory_db}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            results = score_memories(tokens, project_domain, conn, top_n=top_n)
            _log.debug("[CombinationSignalRetriever] scored=%d domain=%s tokens=%d",
                       len(results), project_domain, len(tokens))
            return results
        except Exception as exc:
            _log.error("[CombinationSignalRetriever] score_memories error: %s", exc)
            return []
        finally:
            conn.close()


class KeywordOverlapScorer:
    """Scores tool hints via domain match + keyword overlap against tool_hints.sqlite.

    Wraps the inline scoring logic from ScoreToolsNode.
    Satisfies ToolScorer Protocol.
    """

    # task:53c9f817 — log1p saturates so a heavily-used tool (e.g.
    # contacts__search at ~3687 calls -> log1p=8.2) gets a meaningful edge
    # over an equally domain/keyword-matched but rarely-used tool without
    # letting raw call volume swamp the signal entirely (kw_overlap terms
    # are typically 0-4). Applied only as a tie-breaker on top of an
    # existing domain/keyword match (see `if base > 0` below) — usage
    # count must never manufacture relevance out of nothing, or a
    # frequently-called out-of-domain tool would leak into every result set.
    _COUNT_WEIGHT = 0.3

    def score(
        self,
        keywords: set[str],
        domains: set[str],
        top_n: int = 5,
    ) -> list[dict]:
        import math
        import sqlite3
        from langchain_learning.config import config as _cfg

        if not _cfg.tool_hints_db.exists():
            _log.debug("[KeywordOverlapScorer] tool_hints_db not found: %s", _cfg.tool_hints_db)
            return []
        try:
            conn = sqlite3.connect(f"file:{_cfg.tool_hints_db}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT tool_name, domain, skill, count, keywords FROM mcp_tool_hints"
            ).fetchall()
            conn.close()
        except Exception as exc:
            _log.error("[KeywordOverlapScorer] db error: %s", exc)
            return []

        scored: list[tuple[float, dict]] = []
        for row in rows:
            domain_match = 1.0 if row["domain"] in domains else 0.0
            kw_overlap   = sum(1 for k in keywords if k in (row["keywords"] or ""))
            base         = domain_match * 2 + kw_overlap
            count        = row["count"] or 0
            score        = base + math.log1p(count) * self._COUNT_WEIGHT if base > 0 else 0.0
            if score > 0:
                scored.append((score, {
                    "tool_name": row["tool_name"],
                    "domain":    row["domain"],
                    "skill":     row["skill"] or "",
                    "count":     count,
                }))

        scored.sort(key=lambda x: -x[0])
        result = [h for _, h in scored[:top_n]]
        _log.debug("[KeywordOverlapScorer] returned=%d keywords=%d domains=%s",
                   len(result), len(keywords), domains)
        return result


class DefaultGatePolicy:
    """Dispatches to the GATES dict in hooks/gates.py.

    Wraps hooks.gates.check() to satisfy GatePolicy Protocol.
    """

    def check(self, tool_name: str, ctx: GateContext) -> tuple[bool, str]:
        from hooks.gates import check as _check
        deny, reason = _check(tool_name, ctx)
        _log.debug("[DefaultGatePolicy] tool=%s deny=%s reason=%r", tool_name, deny, reason)
        return deny, reason
