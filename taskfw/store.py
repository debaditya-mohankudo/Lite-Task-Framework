"""TaskStore — data access only.

Deliberately contains NO rule enforcement. Every lifecycle rule lives in
taskfw.lifecycle so it has exactly one implementation, shared by the MCP tools
and the hooks; a store that also enforced rules would be a second place they
could be written, which is the coupling this project exists to avoid.

A store method must therefore never reject a write for a policy reason. It
rejects only what it structurally cannot do.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from taskfw.db.connect import connect, transaction
from taskfw.log import get_logger
from taskfw.scope import for_repo as _scope_for_repo
from taskfw.task import EventKind, Task, utcnow

log = get_logger(__name__)

#: Full-text search is a nice-to-have, not a dependency. FTS5 is a compile-time
#: option in SQLite, and this framework is meant to be portable, so its absence
#: degrades search to LIKE rather than failing at import.
#:
#: Tags have their own column so bm25 can weight them (task:7097f9d4). That
#: needed a new table rather than a changed one: CREATE ... IF NOT EXISTS
#: leaves an existing table's columns alone, and rebuilding it would mean a
#: DROP, which the additive-only migration rule (db/schema.py) forbids. The
#: old single-column `tasks_fts` is left in place, unwritten and unread. It is
#: a derived index, so nothing is lost by leaving it stale.
_FTS_TABLE = "tasks_fts_tagged"
_FTS_DDL = f"CREATE VIRTUAL TABLE IF NOT EXISTS {_FTS_TABLE} USING fts5(id UNINDEXED, tags, text)"
_FTS_INSERT = f"INSERT INTO {_FTS_TABLE} (id, tags, text) VALUES (?,?,?)"


class TaskStore:
    def __init__(self, path: Path | str | None = None, conn: sqlite3.Connection | None = None):
        self.conn = conn if conn is not None else connect(path)
        self.fts = self._try_enable_fts()
        self._active: dict[str, str] = {}

    def _try_enable_fts(self) -> bool:
        try:
            with transaction(self.conn):
                self.conn.execute(_FTS_DDL)
                self._backfill_fts()
            return True
        except sqlite3.OperationalError as exc:
            log.warning("FTS5 unavailable, search falls back to LIKE (%s)", exc)
            return False

    def _backfill_fts(self) -> None:
        """Index every task once, when the FTS table is empty but tasks is not.

        This happens exactly once per database: the first open after
        _FTS_TABLE was introduced. After that, save() keeps it in step. It
        runs in the same transaction as the CREATE, so an interrupted
        backfill leaves no table rather than a half-filled one, which the
        empty check would then never repair.
        """
        if self.conn.execute(f"SELECT 1 FROM {_FTS_TABLE} LIMIT 1").fetchone():
            return
        rows = self.conn.execute("SELECT data FROM tasks").fetchall()
        # Plain inserts, not _index(): the table is empty, and _index's
        # DELETE by an UNINDEXED id scans the whole table per row, which
        # made this O(n^2) (1.5s on 1,861 tasks versus ~0.1s without it).
        self.conn.executemany(
            _FTS_INSERT,
            [self._fts_row(Task.from_json(r["data"])) for r in rows],
        )
        if rows:
            log.info("fts backfill table=%s rows=%d", _FTS_TABLE, len(rows))

    @staticmethod
    def _fts_row(task: Task) -> tuple[str, str, str]:
        return task.id, " ".join(task.tags), task.search_body()

    def _index(self, task: Task) -> None:
        self.conn.execute(f"DELETE FROM {_FTS_TABLE} WHERE id=?", (task.id,))
        self.conn.execute(_FTS_INSERT,
                          self._fts_row(task))

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
            log.info("save task=%s CREATE epic=%s status=%s title=%r",
                     task.id, task.epic, task.status, task.title[:60])
        elif prior["status"] != task.status:
            # A status change is the single most useful thing to see in a log
            # when reconstructing what happened to a task.
            log.info("save task=%s STATUS %s -> %s", task.id, prior["status"], task.status)
        else:
            log.debug("save task=%s UPDATE status=%s", task.id, task.status)
        with transaction(self.conn):
            self.conn.execute(
                """INSERT INTO tasks (id, epic, status, parent, title, data, scope,
                                      created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                     epic=excluded.epic, status=excluded.status, parent=excluded.parent,
                     title=excluded.title, data=excluded.data, scope=excluded.scope,
                     updated_at=excluded.updated_at""",
                (task.id, int(task.epic), task.status, task.parent, task.title,
                 task.to_json(), task.scope, task.created_at, task.updated_at),
            )
            if self.fts:
                self._index(task)
        return task

    def get(self, task_id: str) -> Task | None:
        row = self.conn.execute("SELECT data FROM tasks WHERE id=?", (task_id,)).fetchone()
        return Task.from_json(row["data"]) if row else None

    def list(
        self,
        *,
        status: str | tuple[str, ...] | None = ("open", "blocked"),
        epic: bool | None = None,
        parent: str | None = None,
        scope: str | None = None,
        limit: int = 200,
    ) -> list[Task]:
        """Tasks matching every filter given. None means "do not filter on this".

        `scope` narrows to one project (taskfw/scope.py). Note that it is an
        exact match and therefore EXCLUDES unscoped rows — every task written
        before the column existed. That is the honest behaviour rather than a
        convenient one: silently folding unscoped rows into whichever project
        happened to ask would invent a scope for them, which is the one thing
        this field is not allowed to do. Callers that report counts must say
        how many rows the filter excluded (see accuracy.loop_debt), so a
        narrowed answer can never be mistaken for a complete one.
        """
        where, params = [], []
        if status:
            statuses = (status,) if isinstance(status, str) else tuple(status)
            where.append(f"status IN ({','.join('?' * len(statuses))})")
            params.extend(statuses)
        if epic is not None:
            where.append("epic=?")
            params.append(int(epic))
        if parent is not None:
            where.append("parent=?")
            params.append(parent)
        if scope is not None:
            where.append("scope=?")
            params.append(scope)
        sql = "SELECT data FROM tasks"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        return [Task.from_json(r["data"]) for r in self.conn.execute(sql, params)]

    def children(self, parent_id: str) -> list[Task]:
        return self.list(status=None, parent=parent_id)

    def search(self, query: str, limit: int = 25) -> list[Task]:
        """Full-text search, degrading to LIKE where FTS5 is unavailable.

        `query` is a plain string, not pre-formatted FTS5 syntax — search()
        builds its own OR-of-terms MATCH expression (each term quoted, so
        punctuation is read as literal text, never an FTS5 operator), then
        re-ranks the matched candidates by _combination_score instead of
        FTS5's own bm25 `rank`, so a shared tag outweighs a shared body word.

        The candidate window is taken in bm25 order, never insertion order
        (task:c0c5ff5f): an unordered `LIMIT` returns the oldest matching rows,
        and on a store where most tasks share a common word that window held
        no task created in the last seven weeks. The re-rank sort is stable,
        so bm25 also decides between tasks with equal combination scores —
        which the small integer scores make the usual case, not an edge.
        """
        terms = query.split()
        if not terms:
            return []
        if self.fts:
            try:
                quoted = ['"{}"'.format(t.replace('"', '""')) for t in terms]
                rows = self.conn.execute(
                    f"""SELECT t.data FROM {_FTS_TABLE} f JOIN tasks t ON t.id = f.id
                       WHERE {_FTS_TABLE} MATCH ?
                       ORDER BY bm25({_FTS_TABLE}, 0.0, ?, ?) LIMIT ?""",
                    (" OR ".join(quoted), self._TAG_WEIGHT, self._BODY_WEIGHT, limit * 4),
                ).fetchall()
                tasks = [Task.from_json(r["data"]) for r in rows]
                tasks.sort(key=lambda t: self._combination_score(t, terms), reverse=True)
                return tasks[:limit]
            except sqlite3.OperationalError as exc:
                # A malformed FTS5 query (unbalanced quote, bare operator) is a
                # user-input problem, not a reason to return nothing.
                log.warning("FTS query failed, falling back to LIKE (%s)", exc)
        like = f"%{query}%"
        rows = self.conn.execute(
            "SELECT data FROM tasks WHERE title LIKE ? OR data LIKE ? LIMIT ?", (like, like, limit)
        ).fetchall()
        return [Task.from_json(r["data"]) for r in rows]

    #: Tag overlap outweighs body overlap — a task's tags are a deliberate,
    #: hand-curated signal, while body text is incidental prose. Mirrors
    #: claude-hooks' LoadMemoriesNode combination scorer (task:c3b53021),
    #: which uses the same 3:1 ratio for the same reason.
    _TAG_WEIGHT = 3.0
    _BODY_WEIGHT = 1.0

    @staticmethod
    def _combination_score(task: Task, terms: list[str]) -> float:
        """How well `terms` overlaps task.tags versus its other search text.

        Word-level set intersection, not substring matching — "log" should
        not score against "dialog". Tags and body are scored against
        disjoint word sets so a tag word doesn't get counted twice.
        """
        term_set = {t.lower() for t in terms}
        tag_words = {w.lower() for tag in task.tags for w in re.findall(r"\w+", tag)}
        body_words = {w.lower() for w in re.findall(r"\w+", task.search_text())} - tag_words
        return (len(term_set & tag_words) * TaskStore._TAG_WEIGHT
                + len(term_set & body_words) * TaskStore._BODY_WEIGHT)

    # -- events -------------------------------------------------------------

    def add_event(self, task_id: str, text: str, kind: EventKind = "note") -> None:
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

    def last_event_ts(self, task_id: str, kind: str) -> str | None:
        """The `ts` of the most recent event of `kind` on this task, or None.

        Separate from events() rather than a filter over its result: events()
        takes a limit and returns the most recent rows of EVERY kind, so a
        decision older than that many notes would be missing from it and read
        as absent. This asks the question directly, so "no such event" is the
        only reason it can answer None.
        """
        row = self.conn.execute(
            "SELECT ts FROM task_events WHERE task_id=? AND kind=? ORDER BY id DESC LIMIT 1",
            (task_id, kind),
        ).fetchone()
        return row["ts"] if row else None

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

        Edges must be removable, not merely creatable. An edge that can only be
        added is permanent, so a relation to an abandoned or superseded task
        survives forever and every later reader has to judge for themselves
        whether it still means anything. Returns rows removed.
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
        """Record a commit against a task. Idempotent via UNIQUE(task_id, sha).

        `repo` is normalised through `scope.for_repo` rather than stored as
        given. This column is where free-text project identity was already
        observed fragmenting — five spellings of two projects — so the same
        derivation that produces `Task.scope` produces this, and the two can
        be compared. Rows written before this are left exactly as they are:
        rewriting them would mean inferring a scope from a stale string,
        which is reconstruction, not a record.
        """
        scope_value = _scope_for_repo(repo)
        with transaction(self.conn):
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO task_commits (task_id, sha, repo) VALUES (?,?,?)",
                (task_id, sha, scope_value),
            )
        recorded = cur.rowcount > 0
        log.info("commit task=%s sha=%s repo=%s %s", task_id, sha[:12], scope_value or "-",
                 "recorded" if recorded else "already recorded")
        return recorded

    def commits(self, task_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT sha, repo, ts FROM task_commits WHERE task_id=? ORDER BY id DESC", (task_id,)
        )
        return [dict(r) for r in rows]

    # -- active task ----------------------------------------------------------
    #
    # One task_id per scope, in-memory only. Active status isn't a task
    # property -- it's ephemeral, relevant only while a task is being groomed,
    # implemented, or introspected -- so there is nothing here worth
    # surviving a restart, and no stack: only one task is ever "the one being
    # worked on" for a given scope.

    def set_active(self, task_id: str, scope: str = "global") -> None:
        """Mark task_id as the active task for scope."""
        self._active[scope] = task_id
        log.info("active set task=%s scope=%s", task_id, scope)

    def get_active(self, scope: str = "global") -> str | None:
        """The active task for scope, if any."""
        return self._active.get(scope)

    def clear_active(self, scope: str = "global") -> None:
        """Clear the active task for scope, if any."""
        if self._active.pop(scope, None) is not None:
            log.info("active clear scope=%s", scope)
