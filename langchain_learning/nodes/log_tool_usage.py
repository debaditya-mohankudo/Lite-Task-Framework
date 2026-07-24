"""LogToolUsageNode — upserts tool hint, seeds keywords, appends to prompt_tools state.

Merged from update_tool_keywords: keyword seeding now happens in the same
SQLite connection as the upsert, eliminating a second DB round-trip.
"""
from __future__ import annotations

import importlib
import json
import re
import sqlite3

from langchain_learning.config import config as _cfg
from langchain_learning.nodes._node_log import entry
from langchain_learning.session_state import SessionState
from src.logger import get_logger

_SPLIT_RE = re.compile(r"[_\-]+")

_log = get_logger(__name__)

_MAX_RECENT_PROMPTS = 10

# Tools where a non-empty result means "found" — used by gate prereq checks
_SEARCH_TOOLS = {"contacts__search", "mail__search", "reminders__list", "notes__list"}


def _result_found(tool_name: str, tool_result: dict) -> bool:
    """Return True if the tool result indicates a successful non-empty response."""
    if not tool_result:
        return False
    if tool_name in _SEARCH_TOOLS:
        # contacts__search returns {name, phoneNumbers, ...} — found if phoneNumbers non-empty
        if tool_name == "contacts__search":
            return bool(tool_result.get("phoneNumbers") or tool_result.get("name"))
        return bool(tool_result)
    # For other tools, any non-error result counts as found
    return "error" not in tool_result

_ENSURE_HINTS = """
CREATE TABLE IF NOT EXISTS mcp_tool_hints (
    tool_name       TEXT PRIMARY KEY,
    domain          TEXT,
    count           INTEGER DEFAULT 0,
    last_used       TIMESTAMP,
    avg_latency_ms  REAL DEFAULT 0.0,
    keywords        TEXT DEFAULT '',
    skill           TEXT DEFAULT '',
    recent_prompts  TEXT DEFAULT '[]',
    embedding       BLOB
)
"""


def _derive_keywords(tool_name: str, domain: str, skill: str) -> str:
    """Derive structural keywords from tool name tokens + domain + skill."""
    name = re.sub(r"^mcp__[^_]+__", "", tool_name)
    tokens = {t for t in _SPLIT_RE.split(name) if len(t) >= 3}
    if domain:
        tokens.add(domain)
    if skill:
        tokens.update(t for t in _SPLIT_RE.split(skill) if len(t) >= 3)
    return ",".join(sorted(tokens))


def seed_all_tool_keywords() -> int:
    """task:53c9f817 — proactively seed mcp_tool_hints for every registered MCP
    tool, not just ones that have already been called at least once.

    Without this, a never-invoked tool has empty `keywords` and can only
    surface via domain match — a cold-start gap confirmed directly when
    scratch__* tools had zero rows here until manually inserted. Walks
    src.dispatcher.DOMAIN_MAP (the single source of truth for every
    registered domain__action tool) and derives keywords the same way
    _derive_keywords() does reactively, plus the handler's own docstring
    tokens (dispatcher._wrap() already copies handler.__doc__ onto every
    tool, so this needs no new plumbing).

    Only inserts rows for tools with no existing row — never overwrites a
    tool that already has real usage history (count, recent_prompts,
    reactively-derived keywords). Returns the number of tools newly seeded.
    """
    from src.dispatcher import DOMAIN_MAP
    from core.tool_registry import infer_domain, infer_skill

    tool_hints_db = _cfg.tool_hints_db
    if not tool_hints_db.exists():
        return 0

    seeded = 0
    try:
        with sqlite3.connect(str(tool_hints_db)) as conn:
            conn.execute(_ENSURE_HINTS)
            existing = {r[0] for r in conn.execute("SELECT tool_name FROM mcp_tool_hints").fetchall()}

            for domain, (module_path, actions) in DOMAIN_MAP.items():
                module = importlib.import_module(module_path)
                for action in actions:
                    tool_name = f"{domain}__{action}"
                    if tool_name in existing:
                        continue
                    handler = getattr(module, f"handle_{action}", None)
                    doc_tokens = set()
                    if handler and handler.__doc__:
                        first_line = handler.__doc__.strip().splitlines()[0]
                        doc_tokens = {t.lower() for t in re.findall(r"[A-Za-z]{4,}", first_line)}
                    skill = infer_skill(tool_name)
                    keywords = _derive_keywords(tool_name, infer_domain(tool_name) or domain, skill)
                    if doc_tokens:
                        keywords = ",".join(sorted(set(keywords.split(",")) | doc_tokens))
                    conn.execute(
                        "INSERT INTO mcp_tool_hints (tool_name, domain, count, keywords) VALUES (?, ?, 0, ?)",
                        (tool_name, domain, keywords),
                    )
                    seeded += 1
            conn.commit()
    except Exception as exc:
        _log.warning("[log_tool_usage] seed_all_tool_keywords failed: %s", exc)
        return seeded

    if seeded:
        _log.info("[log_tool_usage] seed_all_tool_keywords: seeded %d new tool(s)", seeded)
    return seeded


def _append_prompt(existing_json: str, new_prompt: str) -> str:
    if not new_prompt:
        return existing_json
    try:
        prompts: list[str] = json.loads(existing_json or "[]")
    except Exception:
        prompts = []
    prompts.append(new_prompt)
    return json.dumps(prompts[-_MAX_RECENT_PROMPTS:])


class LogToolUsageNode:
    """Upsert tool hint row in tool_hints.sqlite.

    Tags: tool-hints, tool-usage, post-tool-use, tool-logging
    """

    _TEST_SESSION_PREFIXES = ("api-test-", "test-", "pytest-")

    def __call__(self, state: SessionState) -> dict:
        from core.tool_registry import infer_domain, infer_skill, strip_mcp_prefix

        tool_name   = state.get("tool_name", "")
        tool_name   = strip_mcp_prefix(tool_name) or tool_name
        duration_ms = float(state.get("duration_ms", 0.0))
        prompt_id   = state.get("prompt_id", "")
        session_id  = state.get("session_id", "") or ""

        entry("log_tool_usage", state, duration_ms=round(duration_ms, 1))

        if not tool_name:
            return {}

        if any(session_id.startswith(p) for p in self._TEST_SESSION_PREFIXES):
            _log.debug("[log_tool_usage] skipping tool_hints upsert for test session=%s", session_id)
            return {}

        domain = infer_domain(tool_name)
        skill  = infer_skill(tool_name)
        prompt = state.get("prompt", "")
        tool_result = state.get("tool_result") or {}
        if tool_name == "contacts__search":
            _log.debug("[log_tool_usage] contacts__search raw result: %s", json.dumps(tool_result, default=str))
        found = _result_found(tool_name, tool_result)

        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=2) as _pool:
            _f1 = _pool.submit(self._upsert_tool_hint, tool_name, domain, skill, duration_ms, prompt)
            _f2 = _pool.submit(self._upsert_task_event_tools, tool_name, prompt_id, state.get("active_task_id", ""))
            _f1.result()
            _f2.result()
        _log.info("[log_tool_usage] tool=%s domain=%s latency=%.1fms found=%s prompt=%s",
                  tool_name, domain, duration_ms, found, prompt_id[:8] if prompt_id else "?")

        existing = list(state.get("prompt_tools") or [])
        existing.append({"tool": tool_name, "found": found})

        import time
        from collections import OrderedDict
        ts = time.time()  # single canonical timestamp for this tool call
        tool_input  = state.get("tool_input") or {}
        session_tools: OrderedDict[str, list[dict]] = OrderedDict(state.get("session_tools") or {})
        if prompt_id:
            bucket = list(session_tools.get(prompt_id) or [])
            bucket.append({"tool": tool_name, "tool_input": tool_input, "ts": ts})
            session_tools[prompt_id] = bucket
            _log.info("[log_tool_usage] session_tools[%s]=%s", prompt_id[:8], [e["tool"] for e in bucket])

        return {"prompt_tools": existing, "session_tools": session_tools}

    def _upsert_task_event_tools(self, tool_name: str, prompt_id: str, task_id: str) -> None:
        """Append tool_name to task_events.tools for the current prompt_id row."""
        if not prompt_id or not task_id:
            return
        tasks_db = _cfg.tasks_db
        if not tasks_db.exists():
            return
        try:
            with sqlite3.connect(str(tasks_db), timeout=5) as conn:
                conn.execute(
                    """UPDATE task_events
                       SET tools = CASE WHEN tools = '' THEN ? ELSE tools || ',' || ? END
                       WHERE prompt_id = ? AND task_id = ?""",
                    (tool_name, tool_name, prompt_id, task_id),
                )
        except Exception as exc:
            _log.warning("[log_tool_usage] task_event tools upsert failed: %s", exc)

    def _upsert_tool_hint(self, short_name: str, domain: str, skill: str, latency_ms: float, prompt_text: str = "") -> None:
        """Upsert tool hint row and seed keywords — single SQLite connection."""
        tool_hints_db = _cfg.tool_hints_db
        if not tool_hints_db.exists():
            return
        try:
            with sqlite3.connect(str(tool_hints_db)) as conn:
                conn.execute(_ENSURE_HINTS)
                cols = {r[1] for r in conn.execute("PRAGMA table_info(mcp_tool_hints)").fetchall()}
                for col, defval in [("keywords", "''"), ("skill", "''"),
                                     ("recent_prompts", "'[]'"), ("embedding", "NULL")]:
                    if col not in cols:
                        conn.execute(f"ALTER TABLE mcp_tool_hints ADD COLUMN {col} TEXT DEFAULT {defval}")

                row = conn.execute(
                    "SELECT count, avg_latency_ms, recent_prompts, keywords FROM mcp_tool_hints WHERE tool_name = ?",
                    (short_name,)
                ).fetchone()

                if row:
                    new_count   = row[0] + 1
                    new_avg     = (row[1] * row[0] + latency_ms) / new_count
                    new_prompts = _append_prompt(row[2] or "[]", prompt_text)
                    # seed keywords if empty (merged from update_tool_keywords)
                    new_kw = row[3] or _derive_keywords(short_name, domain, skill)
                    if not row[3]:
                        _log.info("[log_tool_usage] seeded keywords tool=%s kw=%s", short_name, new_kw)
                    conn.execute(
                        "UPDATE mcp_tool_hints SET count=?, last_used=datetime('now'), avg_latency_ms=?, domain=?, skill=?, recent_prompts=?, keywords=? WHERE tool_name=?",
                        (new_count, round(new_avg, 2), domain, skill, new_prompts, new_kw, short_name),
                    )
                else:
                    new_prompts = _append_prompt("[]", prompt_text)
                    new_kw = _derive_keywords(short_name, domain, skill)
                    _log.info("[log_tool_usage] seeded keywords tool=%s kw=%s", short_name, new_kw)
                    conn.execute(
                        "INSERT INTO mcp_tool_hints (tool_name, domain, count, last_used, avg_latency_ms, skill, recent_prompts, keywords) VALUES (?, ?, 1, datetime('now'), ?, ?, ?, ?)",
                        (short_name, domain, round(latency_ms, 2), skill, new_prompts, new_kw),
                    )
                conn.commit()
        except Exception as exc:
            _log.warning("[log_tool_usage] upsert failed for %r: %s", short_name, exc)
