"""TaskStore — data access only.

Deliberately contains NO rule enforcement. Every lifecycle rule lives in
taskfw.lifecycle so it has exactly one implementation, shared by the MCP tools
and the hooks; a store that also enforced rules would be a second place they
could be written, which is the coupling this project exists to avoid.

A store method must therefore never reject a write for a policy reason. It
rejects only what it structurally cannot do.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from taskfw.db.connect import connect, transaction
from taskfw.log import get_logger
from taskfw.models import Task, utcnow

log = get_logger(__name__)

#: Full-text search is a nice-to-have, not a dependency. FTS5 is a compile-time
#: option in SQLite, and this framework is meant to be portable, so its absence
#: degrades search to LIKE rather than failing at import.
_FTS_DDL = "CREATE VIRTUAL TABLE IF NOT EXISTS tasks_fts USING fts5(id UNINDEXED, text)"


class TaskStore:
    def __init__(self, path: Path | str | None = None, conn: sqlite3.Connection | None = None):
        self.conn = conn if conn is not None else connect(path)
        self.fts = self._try_enable_fts()

    def _try_enable_fts(self) -> bool:
        try:
            self.conn.execute(_FTS_DDL)
            self.conn.commit()
            return True
        except sqlite3.OperationalError as exc:
            log.warning("FTS5 unavailable, search falls back to LIKE (%s)", exc)
            return False

    def close(self) -> None:
        self.conn.close()

    # -- tasks --------------------------------------------------------------

    def save(self, task: Task) -> Task:
        """Insert or update. The sole writer of both the scalar columns and `data`.

        Keeping one writer is what prevents the scalars from drifting out of
        step with the JSON they are projected from.
        """
        task.updated_at = utcnow()
        prior = self.conn.execute("SELECT status FROM tasks WHERE id=?", (task.id,)).fetchone()
        if prior is None:
            log.info("save task=%s CREATE type=%s status=%s title=%r",
                     task.id, task.type, task.status, task.title[:60])
        elif prior["status"] != task.status:
            # A status change is the single most useful thing to see in a log
            # when reconstructing what happened to a task.
            log.info("save task=%s STATUS %s -> %s", task.id, prior["status"], task.status)
        else:
            log.debug("save task=%s UPDATE status=%s", task.id, task.status)
        with transaction(self.conn):
            self.conn.execute(
                """INSERT INTO tasks (id, type, status, parent, title, data, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                     type=excluded.type, status=excluded.status, parent=excluded.parent,
                     title=excluded.title, data=excluded.data, updated_at=excluded.updated_at""",
                (task.id, task.type, task.status, task.parent, task.title,
                 task.to_json(), task.created_at, task.updated_at),
            )
            if self.fts:
                self.conn.execute("DELETE FROM tasks_fts WHERE id=?", (task.id,))
                self.conn.execute(
                    "INSERT INTO tasks_fts (id, text) VALUES (?,?)", (task.id, task.search_text())
                )
        return task

    def get(self, task_id: str) -> Task | None:
        row = self.conn.execute("SELECT data FROM tasks WHERE id=?", (task_id,)).fetchone()
        return Task.from_json(row["data"]) if row else None

    def list(
        self,
        *,
        status: str | tuple[str, ...] | None = ("open", "blocked"),
        type: str | None = None,
        parent: str | None = None,
        limit: int = 200,
    ) -> list[Task]:
        where, params = [], []
        if status:
            statuses = (status,) if isinstance(status, str) else tuple(status)
            where.append(f"status IN ({','.join('?' * len(statuses))})")
            params.extend(statuses)
        if type:
            where.append("type=?")
            params.append(type)
        if parent is not None:
            where.append("parent=?")
            params.append(parent)
        sql = "SELECT data FROM tasks"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        return [Task.from_json(r["data"]) for r in self.conn.execute(sql, params)]

    def children(self, parent_id: str) -> list[Task]:
        return self.list(status=None, parent=parent_id)

    def search(self, query: str, limit: int = 25) -> list[Task]:
        """Full-text search, degrading to LIKE where FTS5 is unavailable."""
        if self.fts:
            try:
                rows = self.conn.execute(
                    """SELECT t.data FROM tasks_fts f JOIN tasks t ON t.id = f.id
                       WHERE tasks_fts MATCH ? ORDER BY rank LIMIT ?""",
                    (query, limit),
                ).fetchall()
                return [Task.from_json(r["data"]) for r in rows]
            except sqlite3.OperationalError as exc:
                # A malformed FTS5 query (unbalanced quote, bare operator) is a
                # user-input problem, not a reason to return nothing.
                log.warning("FTS query failed, falling back to LIKE (%s)", exc)
        like = f"%{query}%"
        rows = self.conn.execute(
            "SELECT data FROM tasks WHERE title LIKE ? OR data LIKE ? LIMIT ?", (like, like, limit)
        ).fetchall()
        return [Task.from_json(r["data"]) for r in rows]

    # -- events -------------------------------------------------------------

    def add_event(self, task_id: str, text: str, kind: str = "note") -> None:
        with transaction(self.conn):
            self.conn.execute(
                "INSERT INTO task_events (task_id, kind, text) VALUES (?,?,?)", (task_id, kind, text)
            )
        log.info("event task=%s kind=%s %r", task_id, kind, text[:80])

    def events(self, task_id: str, limit: int = 100) -> list[dict]:
        rows = self.conn.execute(
            "SELECT ts, kind, text FROM task_events WHERE task_id=? ORDER BY id DESC LIMIT ?",
            (task_id, limit),
        )
        return [dict(r) for r in rows]

    # -- edges --------------------------------------------------------------

    def link(self, from_id: str, to_id: str, rel: str = "relates_to") -> bool:
        """Create an edge. Idempotent — relinking the same edge is a no-op."""
        with transaction(self.conn):
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO task_edges (from_id, to_id, rel) VALUES (?,?,?)",
                (from_id, to_id, rel),
            )
        created = cur.rowcount > 0
        log.info("link %s -%s-> %s %s", from_id, rel, to_id,
                 "created" if created else "already existed")
        return created

    def unlink(self, from_id: str, to_id: str, rel: str | None = None) -> int:
        """Remove an edge, or every edge between two tasks when rel is None.

        Exists because its absence is a real gap: claude-hooks can create and
        read edges but never delete them, so stale edges pointing at abandoned
        tasks accumulate with no way to clear them. Returns rows removed.
        """
        sql = "DELETE FROM task_edges WHERE from_id=? AND to_id=?"
        params: list = [from_id, to_id]
        if rel is not None:
            sql += " AND rel=?"
            params.append(rel)
        with transaction(self.conn):
            cur = self.conn.execute(sql, params)
        log.info("unlink %s -> %s rel=%s removed=%d", from_id, to_id, rel or "*", cur.rowcount)
        return cur.rowcount

    def edges(self, task_id: str) -> dict[str, list[dict]]:
        out = self.conn.execute(
            "SELECT from_id, to_id, rel FROM task_edges WHERE from_id=?", (task_id,)
        ).fetchall()
        inc = self.conn.execute(
            "SELECT from_id, to_id, rel FROM task_edges WHERE to_id=?", (task_id,)
        ).fetchall()
        return {"outgoing": [dict(r) for r in out], "incoming": [dict(r) for r in inc]}

    # -- commits ------------------------------------------------------------

    def add_commit(self, task_id: str, sha: str, repo: str = "") -> bool:
        """Record a commit against a task. Idempotent via UNIQUE(task_id, sha)."""
        with transaction(self.conn):
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO task_commits (task_id, sha, repo) VALUES (?,?,?)",
                (task_id, sha, repo),
            )
        recorded = cur.rowcount > 0
        log.info("commit task=%s sha=%s repo=%s %s", task_id, sha[:12], repo or "-",
                 "recorded" if recorded else "already recorded")
        return recorded

    def commits(self, task_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT sha, repo, ts FROM task_commits WHERE task_id=? ORDER BY id DESC", (task_id,)
        )
        return [dict(r) for r in rows]

    # -- active task --------------------------------------------------------

    def set_active(self, task_id: str, scope: str = "global") -> None:
        """Persist the active-task pointer.

        Durable rather than in-memory on purpose: it is the one piece of
        session-ish state whose loss on restart is a daily annoyance, and a
        one-row table removes that for essentially nothing.
        """
        with transaction(self.conn):
            self.conn.execute(
                """INSERT INTO active_task (scope, task_id, updated_at) VALUES (?,?,?)
                   ON CONFLICT(scope) DO UPDATE SET task_id=excluded.task_id,
                                                    updated_at=excluded.updated_at""",
                (scope, task_id, utcnow()),
            )
        log.info("active task=%s scope=%s", task_id, scope)

    def get_active(self, scope: str = "global") -> str | None:
        row = self.conn.execute("SELECT task_id FROM active_task WHERE scope=?", (scope,)).fetchone()
        return row["task_id"] if row else None

    def clear_active(self, scope: str = "global") -> None:
        with transaction(self.conn):
            self.conn.execute("DELETE FROM active_task WHERE scope=?", (scope,))
        log.info("active cleared scope=%s", scope)
