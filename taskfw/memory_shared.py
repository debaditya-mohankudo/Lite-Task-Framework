"""Loop memory kept in a shared memory DB (TASKFW_MEMORY_DB).

The schema belongs to whoever owns that file. taskfw reads and writes rows
and never creates, migrates or changes the journal mode of anything in it.

Loop memories are the rows with origin='loop'. Task links are rows in
memory_task_links; "superseded" is a memory_links edge of type 'supersedes'
pointing at the row, so a row retired by another tool reads as retired here.

Recall ranks through a throwaway in-memory FTS5 index built from the loop
rows on each call. There is no stored index, so a row written or retired by
another tool cannot leave one stale.
"""
from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from taskfw import config
from taskfw.db.connect import transaction
from taskfw.db.encrypted import open_any
from taskfw.db.schema import _columns_of
from taskfw.memory import _FTS_DDL, MemoryStore, Rejected
from taskfw.task import utcnow

#: What the shared DB must already have.
REQUIRED = {
    "memories": {"id", "name", "type", "tags", "body", "updated", "created",
                 "hit_count", "last_hit", "origin", "kind"},
    "memory_links": {"src_id", "dst_id", "type", "created"},
    "memory_task_links": {"memory_id", "task_id", "relation", "created"},
}

#: The `type` a loop row carries; the owner requires one and `origin` is what
#: tells loop rows apart.
LOOP_TYPE = "feedback"

_SOURCE = """(
    SELECT m.name AS slug, m.kind AS kind, m.body AS text,
           COALESCE((SELECT s.name FROM memory_links l JOIN memories s ON s.id = l.src_id
                     WHERE l.dst_id = m.id AND l.type = 'supersedes'
                     ORDER BY l.created DESC, l.rowid DESC LIMIT 1), '') AS superseded_by,
           COALESCE(m.hit_count, 0) AS hit_count, m.last_hit AS last_hit,
           COALESCE(m.created, m.updated) AS created_at, m.updated AS updated_at
    FROM memories m WHERE m.origin = 'loop'
)"""

_ID = "(SELECT id FROM memories WHERE name = ? AND origin = 'loop')"


class SharedSchemaError(RuntimeError):
    """The shared memory DB is absent or lacks what taskfw needs."""


def _open_waiting(path: Path):
    """Open, retrying while another writer holds the file.

    The opener reads the file to check the key before a busy_timeout can be
    set, so without this a concurrent write fails the open at once.
    """
    deadline = time.monotonic() + config.BUSY_TIMEOUT_MS / 1000
    while True:
        try:
            return open_any(path, timeout=config.BUSY_TIMEOUT_MS / 1000)
        except Exception as exc:
            if "database is locked" not in str(exc) or time.monotonic() >= deadline:
                raise
            time.sleep(0.05)


class SharedMemoryStore(MemoryStore):
    """MemoryStore over a memory DB that taskfw does not own."""

    _source = _SOURCE
    _task_links = "memory_task_links"

    def __init__(self, path: Path | str):
        path = Path(path).expanduser()
        if not path.is_file():
            raise SharedSchemaError(f"TASKFW_MEMORY_DB: no database at {path}")
        self.conn = _open_waiting(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(f"PRAGMA busy_timeout={config.BUSY_TIMEOUT_MS}")
        self._check_schema(path)
        self.fts = True

    def _check_schema(self, path: Path) -> None:
        missing = []
        for table, wanted in REQUIRED.items():
            have = _columns_of(self.conn, table)
            if not have:
                missing.append(f"table {table}")
            else:
                missing.extend(f"column {table}.{c}" for c in sorted(wanted - have))
        if missing:
            self.conn.close()
            raise SharedSchemaError(
                f"TASKFW_MEMORY_DB: {path} lacks {', '.join(missing)}. "
                "Run the owner's migration; taskfw does not change this database."
            )

    def _write(self, slug: str, text: str, task_id: str, kind: str) -> None:
        now = utcnow()
        with transaction(self.conn):
            # `name` is shared with general memories; the WHERE keeps one from
            # being overwritten.
            cur = self.conn.execute(
                """INSERT INTO memories (name, type, tags, body, origin, kind, updated, created)
                   VALUES (?,?,'',?,'loop',?,?,?)
                   ON CONFLICT(name) DO UPDATE SET
                     kind=excluded.kind, body=excluded.body, updated=excluded.updated
                   WHERE memories.origin = 'loop'""",
                (slug, LOOP_TYPE, text.strip(), kind, now, now),
            )
            if cur.rowcount == 0:
                raise Rejected(f"{slug!r} already names a general memory; choose another slug.")
            self._insert_link(slug, task_id, "learned_from")

    def _insert_link(self, slug: str, task_id: str, relation: str) -> int:
        return self.conn.execute(
            f"""INSERT OR IGNORE INTO memory_task_links (memory_id, task_id, relation, created)
                SELECT id, ?, ?, ? FROM memories WHERE id = {_ID}""",
            (task_id, relation, utcnow(), slug),
        ).rowcount

    def _add_link(self, slug: str, task_id: str, relation: str) -> bool:
        with transaction(self.conn):
            added = self._insert_link(slug, task_id, relation)
        return added > 0

    def _mark_superseded(self, slug: str, by: str) -> None:
        now = utcnow()
        with transaction(self.conn):
            # One replacement per memory, as in the task-database store.
            self.conn.execute(
                f"DELETE FROM memory_links WHERE type = 'supersedes' AND dst_id = {_ID}", (slug,)
            )
            self.conn.execute(
                f"""INSERT INTO memory_links (src_id, dst_id, type, created)
                    VALUES ({_ID}, {_ID}, 'supersedes', ?)""",
                (by, slug, now),
            )
            self.conn.execute(
                "UPDATE memories SET updated = ? WHERE name = ? AND origin = 'loop'", (now, slug)
            )

    def _delete(self, slug: str) -> bool:
        # The owner's cascade triggers remove the row's links of both sorts.
        with transaction(self.conn):
            cur = self.conn.execute(
                "DELETE FROM memories WHERE name = ? AND origin = 'loop'", (slug,)
            )
        return cur.rowcount > 0

    def _count_hits(self, slugs: list[str], hit_at: str) -> None:
        with transaction(self.conn):
            self.conn.executemany(
                "UPDATE memories SET hit_count = COALESCE(hit_count, 0) + 1, last_hit = ? "
                "WHERE name = ? AND origin = 'loop'",
                [(hit_at, s) for s in slugs],
            )

    def _links(self, slug: str) -> list:
        return self.conn.execute(
            f"""SELECT task_id, relation FROM memory_task_links
                WHERE memory_id = {_ID} ORDER BY created""",
            (slug,),
        ).fetchall()

    @contextmanager
    def _fts_index(self):
        rows = self.conn.execute(
            "SELECT name, body FROM memories WHERE origin = 'loop' ORDER BY updated, id"
        ).fetchall()
        index = sqlite3.connect(":memory:")
        try:
            index.execute(_FTS_DDL)
            index.executemany(
                "INSERT INTO memories_fts (slug, text) VALUES (?,?)",
                [(r["name"], self._fts_text(r["name"], r["body"])) for r in rows],
            )
            yield index
        finally:
            index.close()
