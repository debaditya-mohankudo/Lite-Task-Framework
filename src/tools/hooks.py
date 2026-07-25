"""MCP tools for querying hook state and logs.

Production uses SqliteSaver (~/.claude/langgraph_checkpoints.db), opened by
hooks/server.py's lifespan() and passed into build_session_graph(checkpointer=...).
MemorySaver only appears as a fallback in langchain_learning/session_graph.py's
get_session_graph(), for standalone/test invocations where the server never set
_graph. checkpoint_query reads that same live SqliteSaver DB directly via raw
sqlite3 — it is NOT deprecated; a prior version of this comment incorrectly
claimed MemorySaver had replaced it in production, which was never verified
against hooks/server.py and was wrong (confirmed 2026-07-05, task:61703ef7).
"""
import json
import sqlite3
import time
import urllib.request
from pathlib import Path
from typing import Optional

import msgpack

_DB_PATH = Path.home() / ".claude" / "langgraph_checkpoints.db"
_HOOKS_LOG_DB = Path.home() / "Library" / "Mobile Documents" / "com~apple~CloudDocs" / "Databases" / "claude_hooks.sqlite"
_SERVER_URL = "http://127.0.0.1:8766"

# One retry with a short sleep absorbs the checkpoint-write race in get_current_session:
# a brand-new session's first checkpoint write can lag a few hundred ms behind the
# UserPromptSubmit hook returning, so an immediate query can see no checkpoint yet.
_SESSION_ID_RETRY_DELAY_S = 0.5


def handle_server_memory(n_events: int = 50) -> dict:
    """Last N events from the server's unified event log — "what was I working on?".

    Free-flowing chronological sequence of prompts, MCP tool calls, task activations,
    and assistant turns with timestamps. SQLite-backed, survives server reloads,
    capped to a rolling window. Returns {error: ...} if the server is unreachable.

    Args:
        n_events: Max recent events to return (default 50).
    """
    url = f"{_SERVER_URL}/session/memory?n_events={int(n_events)}"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        events = data.get("events", [])

        # Group tools under the preceding prompt
        rows: list[tuple[str, list[str]]] = []
        for e in events:
            t = e.get("type")
            if t == "prompt":
                rows.append((e["content"], []))
            elif t == "tool" and rows:
                label = e["content"]
                args = e.get("args")
                if args:
                    import os
                    if "/" in args:
                        # Show path relative to home, trimmed to last 2 components if deep
                        rel = args.replace(os.path.expanduser("~") + "/", "~/")
                        parts = rel.split("/")
                        display = "/".join(parts[-2:]) if len(parts) > 3 else rel
                    else:
                        display = args[:40]
                    label = f"{label}({display})"
                rows[-1][1].append(label)

        lines = ["| # | Prompt | Tools |", "|---|--------|-------|"]
        for i, (prompt, tools) in enumerate(rows, 1):
            prompt_short = prompt[:60] + "…" if len(prompt) > 60 else prompt
            tools_str = ", ".join(tools) if tools else "—"
            lines.append(f"| {i} | {prompt_short} | {tools_str} |")

        return "\n".join(lines)
    except Exception as exc:
        return {"error": f"hook server unreachable ({exc}); server memory is in-process and only available while it runs"}


def _fetch_current_session() -> dict:
    """GET /session/current once. Returns {} on any failure (unreachable, bad JSON, etc.)."""
    try:
        with urllib.request.urlopen(f"{_SERVER_URL}/session/current", timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return {}


def handle_session_id() -> dict:
    """Return the current session's session_id, with one built-in retry.

    Reads from the hook server's live checkpoint (GET /session/current) —
    no active task required, unlike GET /session/active. Retries once after
    a short delay if the first call finds no checkpoint yet: a brand-new
    session's first checkpoint write can lag behind this call by a few hundred
    ms, which is why manually calling the session endpoint twice "fixes" a
    missing session_id — this bakes that retry in so callers don't need to.

    Returns {session_id, turn} on success, or {error: ...} if the server is
    unreachable or no checkpoint exists even after the retry (e.g. queried
    before any hook has fired this session at all).
    """
    result = _fetch_current_session()
    if not result.get("session_id"):
        time.sleep(_SESSION_ID_RETRY_DELAY_S)
        result = _fetch_current_session()
    if not result.get("session_id"):
        return {"error": "No session_id available — hook server unreachable, or no checkpoint written yet for this session"}
    return result


def _decode(value: bytes | None) -> object:
    if value is None:
        return None
    try:
        return msgpack.unpackb(value, raw=False)
    except Exception:
        return value.decode("utf-8", errors="replace")


def handle_checkpoint_query(thread_id: str = "") -> dict:
    """Query the latest LangGraph checkpoint for injected memories, tool hints, session context, domains, and keywords.

    If thread_id is omitted, returns the most recent checkpoint across all threads.

    Reads langgraph_checkpoints.db directly via raw sqlite3, bypassing the SqliteSaver
    API — this is the same DB the live production server actually writes to (confirmed
    2026-07-05, task:61703ef7; a prior version of this docstring incorrectly claimed it
    was deprecated/no longer written). GET /session and hooks__session_id are lighter-weight
    alternatives when only the session_id itself is needed.
    """
    if not _DB_PATH.exists():
        return {"error": f"DB not found: {_DB_PATH}"}

    with sqlite3.connect(_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row

        # Find the latest checkpoint that has a 'memories' write (skip stop/output events)
        if thread_id:
            row = conn.execute(
                """SELECT c.thread_id, c.checkpoint_id FROM checkpoints c
                   JOIN writes w ON c.thread_id = w.thread_id AND c.checkpoint_id = w.checkpoint_id
                   WHERE c.thread_id = ? AND w.channel = 'memories'
                   ORDER BY c.checkpoint_id DESC LIMIT 1""",
                (thread_id,),
            ).fetchone()
        else:
            row = conn.execute(
                """SELECT c.thread_id, c.checkpoint_id FROM checkpoints c
                   JOIN writes w ON c.thread_id = w.thread_id AND c.checkpoint_id = w.checkpoint_id
                   WHERE w.channel = 'memories'
                   ORDER BY c.checkpoint_id DESC LIMIT 1"""
            ).fetchone()

        if not row:
            return {"error": "No checkpoints found"}

        tid = row["thread_id"]
        cid = row["checkpoint_id"]

        writes = conn.execute(
            "SELECT channel, value FROM writes WHERE thread_id = ? AND checkpoint_id = ?",
            (tid, cid),
        ).fetchall()

    channels = {w["channel"]: _decode(w["value"]) for w in writes}

    memories_raw = channels.get("memories", [])
    memories = []
    if isinstance(memories_raw, list):
        for m in memories_raw:
            if isinstance(m, dict):
                memories.append({
                    "name": m.get("name"),
                    "type": m.get("type"),
                    "domain": m.get("domain"),
                    "tags": m.get("tags"),
                    "body": (m.get("body") or "")[:200],
                })

    tool_hints_raw = channels.get("tool_hints", [])
    tool_hints = []
    if isinstance(tool_hints_raw, list):
        for h in tool_hints_raw:
            if isinstance(h, dict):
                tool_hints.append({
                    "tool": h.get("tool_name"),
                    "domain": h.get("domain"),
                    "skill": h.get("skill"),
                    "count": h.get("count"),
                })

    session_context_raw = channels.get("session_context", [])
    session_context = []
    if isinstance(session_context_raw, list):
        for s in session_context_raw:
            session_context.append(str(s)[:300] if s else "")

    return {
        "thread_id": tid,
        "checkpoint_id": cid,
        "prompt_id": channels.get("prompt_id"),
        "domains": channels.get("domains", []),
        "keywords": channels.get("keywords", []),
        "matched_keywords": channels.get("matched_keywords"),
        "memories": memories,
        "tool_hints": tool_hints,
        "session_context": session_context,
        "event_type": channels.get("event_type"),
        "cwd": channels.get("cwd"),
        "turn": channels.get("turn"),
    }


def _toon_cell(value: object) -> str:
    s = "" if value is None else str(value)
    if any(c in s for c in (",", "\n", '"')):
        return '"' + s.replace('"', '""') + '"'
    return s


def _rows_to_toon(rows: list[dict]) -> str:
    """Encode a list of uniform-schema dicts as TOON's tabular array format.

    header[N]{field1,field2,...}:
      val1,val2,...
    """
    if not rows:
        return "rows[0]{}:"
    fields = list(rows[0].keys())
    lines = [f"rows[{len(rows)}]{{{','.join(fields)}}}:"]
    for r in rows:
        lines.append("  " + ",".join(_toon_cell(r.get(f)) for f in fields))
    return "\n".join(lines)


def handle_read_logs_sqlite(
    level: str = "",
    logger: str = "",
    search: str = "",
    limit: int = 50,
    format: str = "toon",
) -> dict | str:
    """Query hook_logs from claude_hooks.sqlite.

    Args:
        level:  Filter by log level (e.g. INFO, WARNING, ERROR). Empty = all.
        logger: Filter by logger name substring. Empty = all.
        search: Substring to match in the message field. Empty = all.
        limit:  Max rows to return (default 50, max 200).
        format: "toon" (default, token-efficient tabular text) or "json" (dict).
                Rows are a uniform schema (id, ts, level, logger, message), which is
                exactly what TOON's tabular encoding was designed to compress —
                falls back to "json" for callers that need a dict/error shape.
    """
    if not _HOOKS_LOG_DB.exists():
        return {"error": f"DB not found: {_HOOKS_LOG_DB}"}

    limit = min(limit, 200)
    conditions = []
    params: list = []

    if level:
        conditions.append("level = ?")
        params.append(level.upper())
    if logger:
        conditions.append("logger LIKE ?")
        params.append(f"%{logger}%")
    if search:
        conditions.append("message LIKE ?")
        params.append(f"%{search}%")

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    params.append(limit)

    with sqlite3.connect(_HOOKS_LOG_DB) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"SELECT id, ts, level, logger, message FROM hook_logs {where} ORDER BY id DESC LIMIT ?",
            params,
        ).fetchall()

    dict_rows = [dict(r) for r in rows]

    if format == "toon":
        return f"count: {len(dict_rows)}\n{_rows_to_toon(dict_rows)}"

    return {
        "count": len(dict_rows),
        "rows": dict_rows,
    }
