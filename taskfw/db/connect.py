"""Connection helper — the single place connection pragmas are set.

Every caller goes through connect(). WAL and busy_timeout are applied here
rather than left to callers because getting them right exactly once is the
whole point: several processes (an MCP server per host window, plus hook
invocations) write this file concurrently, and a caller that forgets is a
lock error under load rather than an obvious failure.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from taskfw import config
from taskfw.db.schema import migrate

_migrated: set[str] = set()


def connect(path: Path | str | None = None, *, migrate_once: bool = True) -> sqlite3.Connection:
    """Open a configured connection, running migrations on first use per path."""
    target = Path(path) if path is not None else config.db_path()
    if path is not None:
        Path(target).parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(target), timeout=config.BUSY_TIMEOUT_MS / 1000)
    conn.row_factory = sqlite3.Row

    # WAL lets readers proceed while a writer holds the file, which is the
    # normal case here — several agent processes reading, one writing.
    # Skipped for :memory:, where WAL is not applicable.
    if str(target) != ":memory:":
        conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(f"PRAGMA busy_timeout={config.BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA foreign_keys=ON")

    key = str(target)
    if migrate_once and key not in _migrated:
        migrate(conn)
        _migrated.add(key)
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Commit on success, roll back on any exception."""
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
