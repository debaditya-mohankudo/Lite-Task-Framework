"""Session-scoped in-memory transient key-value store (task:a1181fe6).

Not persisted anywhere — a plain module-level dict held by the hook server
process. Gone on server restart, by design (confirmed with user 2026-07-24):
this is for short-lived working data during a session (e.g. a running list
of candidate concepts while sweeping many files, before deciding what's
worth a real write), not durable storage. For durable, task-scoped
decisions use tasks__add_decision; for durable, cross-session knowledge use
memory__add.

Two caps keep this from becoming an unbounded blob store over a long server
uptime:
- _MAX_VALUE_BYTES: a single value's JSON-serialized size limit.
- _MAX_SESSIONS: once more distinct session_ids than this have written data,
  the oldest (by first-write order) is evicted wholesale. Plain dicts keep
  insertion order in Python 3.7+, so this is a cheap FIFO, not a real LRU —
  a session that writes once and goes quiet is evicted before one still
  being actively read/written, even if the latter was created first. That
  tradeoff is intentional: tracking last-access order for an eviction path
  that rarely fires isn't worth the extra bookkeeping.
"""
from __future__ import annotations

import json
from typing import Any

_MAX_VALUE_BYTES = 8192
_MAX_SESSIONS = 500

_SCRATCH: dict[str, dict[str, Any]] = {}


def _evict_if_needed() -> None:
    while len(_SCRATCH) > _MAX_SESSIONS:
        oldest_session_id = next(iter(_SCRATCH))
        del _SCRATCH[oldest_session_id]


def handle_set(session_id: str, key: str, value: Any) -> dict:
    """Set a scratch key for a session. Overwrites any existing value for
    that key. Returns {"error": ...} if the value is too large to store."""
    try:
        size = len(json.dumps(value))
    except TypeError as exc:
        return {"error": f"value is not JSON-serializable: {exc}"}
    if size > _MAX_VALUE_BYTES:
        return {"error": f"value too large ({size} bytes > {_MAX_VALUE_BYTES} byte limit)"}

    is_new_session = session_id not in _SCRATCH
    _SCRATCH.setdefault(session_id, {})[key] = value
    if is_new_session:
        _evict_if_needed()
    return {"ok": True, "session_id": session_id, "key": key}


def handle_get(session_id: str, key: str) -> dict:
    """Get a scratch value by key. {"found": False} if the session or key
    doesn't exist (never raises on a missing key)."""
    session = _SCRATCH.get(session_id)
    if session is None or key not in session:
        return {"found": False}
    return {"found": True, "value": session[key]}


def handle_list(session_id: str) -> dict:
    """All key/value pairs currently held for a session. Empty dict if the
    session has nothing scratched (not an error)."""
    return {"session_id": session_id, "items": dict(_SCRATCH.get(session_id, {}))}


def handle_delete(session_id: str, key: str) -> dict:
    """Delete one key from a session's scratch data. No-op (not an error)
    if the key/session didn't exist."""
    session = _SCRATCH.get(session_id)
    existed = session is not None and key in session
    if session is not None:
        session.pop(key, None)
    return {"ok": True, "deleted": existed}


def handle_clear(session_id: str) -> dict:
    """Drop all scratch data for a session. No-op (not an error) if the
    session had nothing."""
    existed = session_id in _SCRATCH
    _SCRATCH.pop(session_id, None)
    return {"ok": True, "cleared": existed}
