"""Logging — stderr always, plus a persistent sink for the rest.

An MCP server speaks JSON-RPC over stdout, so anything written there corrupts
the protocol — that bans stdout, not persistence, and neither SQLite nor a
plain file falls under it. Two jobs: stderr is for whoever is watching the
process live; the second sink is what's left once that process exits — a
queryable dimension for watching how the framework behaves over time,
alongside task_events' durable record of what was decided and shipped.

Two implementations of that second sink, chosen by TASKFW_LOG_JSONL:
SQLiteHandler in normal operation, persisting into the `logs` table in the
same tasks.db everything else lives in. JsonlHandler when that env var is
set — tests/conftest.py sets it so a test run never adds a single row to
`logs`, isolated test database or not: simpler to inspect, and nothing to
accumulate or prune across repeated local runs. One file, truncated fresh
each run rather than appended across them.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time

from taskfw import config
from taskfw.db.connect import connect

_configured = False


class SQLiteHandler(logging.Handler):
    """Persists log records into the `logs` table.

    Global and out-of-band, matching get_logger() itself being a process-wide
    singleton: always targets config.db_path(), never whatever TaskStore is
    currently swapped in via mcp_server.set_store() — lifecycle.py's check_*
    functions are pure by design, with no store to draw a path from, so
    there's nothing to thread a per-call path through. tasks__logs reads the
    same fixed path for the same reason, deliberately not store().conn.

    Never raises: a broken log write must not break the tool call that
    triggered it, per this project's own rule that a convenience's worst
    failure mode is being missing, never being blocking.

    One connection per thread, not one per process: sqlite3 connections are
    bound to the thread that opened them, and an MCP server dispatches sync
    tool calls across a worker thread pool, so a single process-wide
    connection eventually gets used from a thread that didn't create it —
    sqlite3 raises ProgrammingError on that, which the bare except below used
    to swallow silently for the rest of the process's life (task:7fff42f4).
    threading.local makes that reuse unrepresentable instead of catching it.
    """

    def __init__(self) -> None:
        super().__init__()
        self._local = threading.local()

    def _connection(self):
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = connect()
            self._local.conn = conn
        return conn

    def emit(self, record: logging.LogRecord) -> None:
        try:
            conn = self._connection()
            conn.execute(
                "INSERT INTO logs (logger, level, message) VALUES (?, ?, ?)",
                (record.name, record.levelname, record.getMessage()),
            )
            conn.commit()
        except Exception:
            pass


class JsonlHandler(logging.Handler):
    """Test-mode sink — one JSON object per line, in a file truncated fresh per run.

    Never touches the `logs` table. Selected instead of SQLiteHandler only
    when TASKFW_LOG_JSONL is set — see this module's own docstring.

    Never raises, same rule as SQLiteHandler: a broken log write must not
    break the tool call that triggered it.
    """

    def __init__(self, path: str) -> None:
        super().__init__()
        self._path = path
        open(self._path, "w").close()  # fresh file once, at construction

    def emit(self, record: logging.LogRecord) -> None:
        try:
            line = json.dumps({
                "logger": record.name,
                "level": record.levelname,
                "message": record.getMessage(),
                "ts": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(record.created)),
            })
            with open(self._path, "a") as f:
                f.write(line + "\n")
        except Exception:
            pass


def get_logger(name: str) -> logging.Logger:
    global _configured
    if not _configured:
        stderr_handler = logging.StreamHandler(sys.stderr)
        stderr_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        root = logging.getLogger("taskfw")
        root.addHandler(stderr_handler)
        jsonl_path = os.environ.get("TASKFW_LOG_JSONL")
        root.addHandler(JsonlHandler(jsonl_path) if jsonl_path else SQLiteHandler())
        root.setLevel(config.LOG_LEVEL)
        root.propagate = False
        _configured = True
    return logging.getLogger(f"taskfw.{name}" if not name.startswith("taskfw") else name)
