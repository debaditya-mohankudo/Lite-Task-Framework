"""Central SQLite DDL definitions and migrate() functions.

Purpose: this module is NOT the production schema/migration path. Every tool
module (src/tools/tasks.py, memory.py, scratch.py, hooks.py) connects to its
own DB directly and manages its own schema independently — most assume
scripts/init_db.py already ran once; src/tools/tasks.py additionally
self-heals via its own inline _ensure_db()/_migrate() on every connect. This
split is deliberate (task:cb357eb6, commit de1ae61, 2026-06-27): it lets test
fixtures and the one-time installer share DDL without giving schema.py any
runtime authority over production connect-time behavior.

Usage:
- Tests: import DDL constants to build fixtures (no inline DDL in test files).
- Setup: call migrate_*() once on first install via scripts/init_db.py.
- Prod connect-time code (_ensure_db, _SCHEMA, etc.) is NOT replaced — it stays as-is.

Adding a column/table: add it to the DDL constant + migrate_*() here, AND to the
matching tool module's own inline schema code if that module self-heals (e.g.
src/tools/tasks.py's _ensure_db()/_migrate()) — the two must be kept in sync by
hand. See tests/test_task_issue_type.py::TestSchemaParity (task:9d3acbef) for
the automated check that catches drift between the two.
"""
from __future__ import annotations

import sqlite3

# ── MEMORY.sqlite ─────────────────────────────────────────────────────────────

MEMORIES_DDL = """
CREATE TABLE IF NOT EXISTS memories (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT UNIQUE NOT NULL,
    type           TEXT NOT NULL,
    domain         TEXT DEFAULT 'global',
    tags           TEXT DEFAULT '',
    body           TEXT DEFAULT '',
    related        TEXT DEFAULT '',
    updated        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_validated TIMESTAMP,
    files          TEXT,
    docs           TEXT,
    hit_count      INTEGER DEFAULT 0,
    last_hit       TIMESTAMP
)
"""


def migrate_memory_db(conn: sqlite3.Connection) -> None:
    """Additive migrations for MEMORY.sqlite."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(memories)")}
    additive = {
        "related":        "TEXT DEFAULT ''",
        "last_validated": "TIMESTAMP",
        "files":          "TEXT",
        "docs":           "TEXT",
        "hit_count":      "INTEGER DEFAULT 0",
        "last_hit":       "TIMESTAMP",
    }
    for col, typedef in additive.items():
        if col not in cols:
            conn.execute(f"ALTER TABLE memories ADD COLUMN {col} {typedef}")
    conn.commit()


# ── proj_tasks.db ─────────────────────────────────────────────────────────────

OPEN_TASKS_DDL = """
CREATE TABLE IF NOT EXISTS open_tasks (
    id         TEXT PRIMARY KEY,
    title      TEXT NOT NULL,
    body       TEXT DEFAULT '',
    tags       TEXT DEFAULT '',
    status     TEXT DEFAULT 'open',
    issue_type TEXT DEFAULT 'task',
    parent_id  TEXT DEFAULT NULL REFERENCES open_tasks(id),
    keywords   TEXT DEFAULT NULL,
    created_at TIMESTAMP DEFAULT (datetime('now')),
    updated_at TIMESTAMP DEFAULT (datetime('now')),
    groomed_at TIMESTAMP DEFAULT NULL
)
"""

TASK_EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS task_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id    TEXT NOT NULL,
    prompt_id  TEXT DEFAULT '',
    session_id TEXT DEFAULT '',
    turn       INTEGER DEFAULT 0,
    summary    TEXT DEFAULT '',
    tools      TEXT DEFAULT '',
    related    TEXT DEFAULT '',
    memories   TEXT DEFAULT '',
    logged_at  TIMESTAMP DEFAULT (datetime('now')),
    FOREIGN KEY (task_id) REFERENCES open_tasks(id) ON DELETE CASCADE
)
"""

# Also created by src/tools/tasks.py's own _ensure_db() (task:9d3acbef) — that's
# the actual production path; this constant is for test fixtures / one-time
# install only, per this module's docstring. Kept in sync manually; see the
# schema-parity test in tests/test_task_issue_type.py for the automated guard.
TASK_EDGES_DDL = """
CREATE TABLE IF NOT EXISTS task_edges (
    from_id       TEXT NOT NULL,
    to_id         TEXT NOT NULL,
    relation_type TEXT NOT NULL,
    created_at    TIMESTAMP DEFAULT (datetime('now')),
    PRIMARY KEY (from_id, to_id, relation_type)
)
"""


def migrate_tasks_db(conn: sqlite3.Connection) -> None:
    """Additive migrations for proj_tasks.db."""
    task_cols = {r[1] for r in conn.execute("PRAGMA table_info(open_tasks)")}
    task_additive = {
        "issue_type": "TEXT DEFAULT 'task'",
        "parent_id":  "TEXT DEFAULT NULL",
        "keywords":   "TEXT DEFAULT NULL",
        "groomed_at": "TIMESTAMP DEFAULT NULL",
    }
    for col, typedef in task_additive.items():
        if col not in task_cols:
            conn.execute(f"ALTER TABLE open_tasks ADD COLUMN {col} {typedef}")

    event_cols = {r[1] for r in conn.execute("PRAGMA table_info(task_events)")}
    event_additive = {
        "related":  "TEXT DEFAULT ''",
        "memories": "TEXT DEFAULT ''",
    }
    for col, typedef in event_additive.items():
        if col not in event_cols:
            conn.execute(f"ALTER TABLE task_events ADD COLUMN {col} {typedef}")

    conn.commit()


# ── tool_hints.sqlite ─────────────────────────────────────────────────────────

MCP_TOOL_HINTS_DDL = """
CREATE TABLE IF NOT EXISTS mcp_tool_hints (
    tool_name      TEXT PRIMARY KEY,
    domain         TEXT,
    count          INTEGER DEFAULT 0,
    last_used      TIMESTAMP,
    avg_latency_ms REAL DEFAULT 0.0,
    keywords       TEXT DEFAULT '',
    skill          TEXT DEFAULT '',
    recent_prompts TEXT DEFAULT '[]',
    embedding      BLOB
)
"""


def migrate_tool_hints_db(conn: sqlite3.Connection) -> None:
    """Additive migrations for tool_hints.sqlite."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(mcp_tool_hints)")}
    additive = {
        "skill":          "TEXT DEFAULT ''",
        "recent_prompts": "TEXT DEFAULT '[]'",
        "embedding":      "BLOB",
    }
    for col, typedef in additive.items():
        if col not in cols:
            conn.execute(f"ALTER TABLE mcp_tool_hints ADD COLUMN {col} {typedef}")
    conn.commit()


# ── claude_hooks.sqlite ───────────────────────────────────────────────────────

HOOK_LOGS_DDL = """
CREATE TABLE IF NOT EXISTS hook_logs (
    id      INTEGER PRIMARY KEY,
    logger  TEXT      NOT NULL,
    level   TEXT      NOT NULL,
    message TEXT      NOT NULL,
    ts      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""

TEST_RUNS_DDL = """
CREATE TABLE IF NOT EXISTS test_runs (
    run_id   TEXT PRIMARY KEY,
    ts       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    n_tests  INTEGER NOT NULL DEFAULT 0,
    n_passed INTEGER NOT NULL DEFAULT 0,
    n_failed INTEGER NOT NULL DEFAULT 0
)
"""


def migrate_hooks_db(conn: sqlite3.Connection) -> None:
    """Additive migrations for claude_hooks.sqlite."""
    # No additive columns yet — placeholder for future migrations.
    conn.commit()
