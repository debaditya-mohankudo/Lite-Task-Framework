"""Schema — the single declarative source of truth for the database.

Every consumer (migration, test fixtures, tooling) reads TABLES from here.
Nothing restates the DDL, so there is no second definition to keep in sync
and no parity test needed to police one.

ADDITIVE-ONLY MIGRATION is enforced structurally rather than by convention:
migrate() can emit exactly two statements, CREATE TABLE IF NOT EXISTS and
ALTER TABLE ADD COLUMN. There is no code path here that drops or rewrites a
column, so a destructive migration cannot be written by accident. A column
removed from TABLES is reported as orphaned, never dropped.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Column:
    name: str
    decl: str  # e.g. "TEXT NOT NULL", "TEXT DEFAULT ''"

    def sql(self) -> str:
        return f"{self.name} {self.decl}"


@dataclass(frozen=True)
class Table:
    name: str
    columns: tuple[Column, ...]
    #: Table-level constraints (PRIMARY KEY, UNIQUE, FOREIGN KEY) that cannot
    #: be expressed per-column. Kept separate because ALTER TABLE ADD COLUMN
    #: cannot add them — anything here is fixed at creation time.
    constraints: tuple[str, ...] = field(default_factory=tuple)

    def create_sql(self) -> str:
        parts = [c.sql() for c in self.columns] + list(self.constraints)
        body = ",\n    ".join(parts)
        return f"CREATE TABLE IF NOT EXISTS {self.name} (\n    {body}\n)"

    def column_names(self) -> set[str]:
        return {c.name for c in self.columns}


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------

TASKS = Table(
    name="tasks",
    columns=(
        Column("id", "TEXT PRIMARY KEY"),
        # Two issue types only: 'epic' groups, 'task' does work. No story /
        # bug / subtask, so no hierarchy matrix — see taskfw/lifecycle.py.
        Column("type", "TEXT NOT NULL DEFAULT 'task'"),
        Column("status", "TEXT NOT NULL DEFAULT 'open'"),
        Column("parent", "TEXT DEFAULT NULL"),
        Column("title", "TEXT NOT NULL DEFAULT ''"),
        # The full typed task object as JSON. Authoritative for every field
        # that is not a scalar column above; the scalars exist only so the
        # common filters (status, type, parent) are indexable without
        # json_extract on every row. TaskStore.save() is the sole writer of
        # both, which is what keeps them from drifting.
        Column("data", "TEXT NOT NULL DEFAULT '{}'"),
        Column("created_at", "TEXT NOT NULL DEFAULT (datetime('now'))"),
        Column("updated_at", "TEXT NOT NULL DEFAULT (datetime('now'))"),
    ),
)

TASK_EVENTS = Table(
    name="task_events",
    columns=(
        Column("id", "INTEGER PRIMARY KEY AUTOINCREMENT"),
        Column("task_id", "TEXT NOT NULL"),
        Column("ts", "TEXT NOT NULL DEFAULT (datetime('now'))"),
        # 'decision' | 'note' | 'status' | anything a caller finds useful.
        Column("kind", "TEXT NOT NULL DEFAULT 'note'"),
        Column("text", "TEXT NOT NULL DEFAULT ''"),
    ),
    constraints=("FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE",),
)

TASK_EDGES = Table(
    name="task_edges",
    columns=(
        Column("from_id", "TEXT NOT NULL"),
        Column("to_id", "TEXT NOT NULL"),
        Column("rel", "TEXT NOT NULL"),
        Column("created_at", "TEXT NOT NULL DEFAULT (datetime('now'))"),
    ),
    # Makes linking the same edge twice a no-op rather than a duplicate row.
    constraints=("PRIMARY KEY (from_id, to_id, rel)",),
)

TASK_COMMITS = Table(
    name="task_commits",
    columns=(
        Column("id", "INTEGER PRIMARY KEY AUTOINCREMENT"),
        Column("task_id", "TEXT NOT NULL"),
        Column("sha", "TEXT NOT NULL"),
        Column("repo", "TEXT NOT NULL DEFAULT ''"),
        Column("ts", "TEXT NOT NULL DEFAULT (datetime('now'))"),
    ),
    # Makes commit capture idempotent — the same commit observed twice by a
    # re-fired hook does not create a second row.
    constraints=(
        "UNIQUE (task_id, sha)",
        "FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE",
    ),
)

ACTIVE_TASK = Table(
    name="active_task",
    columns=(
        # Usually a workspace path; 'global' when the caller has no workspace.
        Column("scope", "TEXT PRIMARY KEY"),
        Column("task_id", "TEXT NOT NULL"),
        Column("updated_at", "TEXT NOT NULL DEFAULT (datetime('now'))"),
    ),
)

TABLES: tuple[Table, ...] = (TASKS, TASK_EVENTS, TASK_EDGES, TASK_COMMITS, ACTIVE_TASK)

INDEXES: tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)",
    "CREATE INDEX IF NOT EXISTS idx_tasks_parent ON tasks(parent)",
    "CREATE INDEX IF NOT EXISTS idx_events_task ON task_events(task_id)",
    "CREATE INDEX IF NOT EXISTS idx_commits_task ON task_commits(task_id)",
)


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------

def migrate(conn: sqlite3.Connection) -> dict[str, list[str]]:
    """Bring the database up to TABLES. Additive only.

    Returns {"created": [...], "added": ["table.column", ...],
             "orphaned": ["table.column", ...]} — orphaned columns exist in the
    database but not in TABLES. They are reported so a drift is visible, and
    deliberately left in place: dropping them is the one thing this function
    must never do.
    """
    created: list[str] = []
    added: list[str] = []
    orphaned: list[str] = []

    for table in TABLES:
        existing = _columns_of(conn, table.name)
        if not existing:
            conn.execute(table.create_sql())
            created.append(table.name)
            continue

        for col in table.columns:
            if col.name not in existing:
                # ALTER TABLE ADD COLUMN rejects non-constant defaults such as
                # datetime('now'), so new timestamp columns land as NULL-able
                # and are populated by the writer instead.
                decl = _addable_decl(col.decl)
                conn.execute(f"ALTER TABLE {table.name} ADD COLUMN {col.name} {decl}")
                added.append(f"{table.name}.{col.name}")

        orphaned.extend(f"{table.name}.{c}" for c in sorted(existing - table.column_names()))

    for index_sql in INDEXES:
        conn.execute(index_sql)

    conn.commit()
    return {"created": created, "added": added, "orphaned": orphaned}


def _columns_of(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _addable_decl(decl: str) -> str:
    """Strip a non-constant DEFAULT, which ALTER TABLE ADD COLUMN cannot accept."""
    if "DEFAULT (" in decl:
        return decl.split("DEFAULT (")[0].strip().replace("NOT NULL", "").strip()
    return decl
