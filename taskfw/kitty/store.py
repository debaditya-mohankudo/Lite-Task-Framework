"""kitty-taskfw's own SQLite: task -> window/tab -> claude_session_id.

Separate from ~/.taskfw/tasks.db on purpose (view-layer state about kitty windows; no
migration on the DB shared by every repo). One row per launched tab. The task of a row is
immutable; claude_session_id is null at launch and backfilled by the SessionStart hook.
"""
from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PATH = Path(os.environ.get("KITTY_TASKFW_STORE", "~/.kitty-taskfw/store.sqlite")).expanduser()

SCHEMA = """
CREATE TABLE IF NOT EXISTS tabs (
  id INTEGER PRIMARY KEY,
  task_id TEXT NOT NULL,
  window_id INTEGER NOT NULL,
  tab_id INTEGER,
  socket TEXT NOT NULL,
  cwd TEXT NOT NULL,
  claude_session_id TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  closed_at TEXT,
  closed_reason TEXT
);
CREATE INDEX IF NOT EXISTS tabs_task ON tabs(task_id);
CREATE UNIQUE INDEX IF NOT EXISTS tabs_window ON tabs(socket, window_id);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY,
  socket TEXT NOT NULL,
  window_id INTEGER NOT NULL,
  task_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  data TEXT,
  ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS events_window ON events(socket, window_id, id);
"""


@dataclass(frozen=True)
class TabRow:
    id: int
    task_id: str
    window_id: int
    tab_id: int | None
    socket: str
    cwd: str
    claude_session_id: str | None
    created_at: str
    closed_at: str | None
    closed_reason: str | None = None


class Store:
    def __init__(self, path: str | Path = DEFAULT_PATH):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = str(path)
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        cols = {r["name"] for r in self.db.execute("PRAGMA table_info(tabs)")}
        if "closed_reason" not in cols:  # store file created before hibernation existed
            self.db.execute("ALTER TABLE tabs ADD COLUMN closed_reason TEXT")
            self.db.commit()

    def record_launch(self, *, task_id: str, window_id: int, tab_id: int | None,
                      socket: str, cwd: str) -> int:
        cur = self.db.execute(
            "INSERT INTO tabs(task_id, window_id, tab_id, socket, cwd) VALUES (?,?,?,?,?)",
            (task_id, window_id, tab_id, socket, cwd))
        self.db.commit()
        return cur.lastrowid

    def backfill_session(self, *, socket: str, window_id: int, session_id: str) -> bool:
        """Called from SessionStart. Returns False if no row matches that window."""
        cur = self.db.execute(
            "UPDATE tabs SET claude_session_id=? WHERE socket=? AND window_id=? AND closed_at IS NULL",
            (session_id, socket, window_id))
        self.db.commit()
        return cur.rowcount > 0

    def events(self, task_id: str | None = None) -> list[sqlite3.Row]:
        q, a = "SELECT * FROM events", ()
        if task_id:
            q, a = q + " WHERE task_id=?", (task_id,)
        return list(self.db.execute(q + " ORDER BY id", a))

    def mark_closed(self, row_id: int, reason: str = "closed") -> None:
        self.db.execute("UPDATE tabs SET closed_at=datetime('now'), closed_reason=? WHERE id=?",
                        (reason, row_id))
        self.db.commit()

    def latest_for_task(self, task_id: str) -> TabRow | None:
        r = self.db.execute("SELECT * FROM tabs WHERE task_id=? ORDER BY id DESC LIMIT 1",
                            (task_id,)).fetchone()
        return TabRow(**dict(r)) if r else None

    def latest_session_for_task(self, task_id: str) -> str | None:
        r = self.db.execute(
            "SELECT claude_session_id FROM tabs WHERE task_id=? AND claude_session_id IS NOT NULL "
            "ORDER BY id DESC LIMIT 1", (task_id,)).fetchone()
        return r[0] if r else None

    def list(self, open_only: bool = False) -> list[TabRow]:
        q = "SELECT * FROM tabs" + (" WHERE closed_at IS NULL" if open_only else "") + " ORDER BY id"
        return [TabRow(**dict(r)) for r in self.db.execute(q)]
