"""Foundation tests — schema, migration, and connection pragmas.

The migration tests exist to pin the AdditiveOnlyMigration requirement: a
destructive migration should be impossible to write here, not merely
discouraged.
"""
from __future__ import annotations

import sqlite3

import pytest

from taskfw.db.connect import connect
from taskfw.db.schema import TABLES, Column, Table, migrate


@pytest.fixture
def db(tmp_path):
    conn = connect(tmp_path / "t.db")
    yield conn
    conn.close()


class TestSchemaCreation:
    def test_every_declared_table_is_created(self, db):
        present = {r["name"] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in TABLES:
            assert table.name in present

    def test_declared_columns_match_the_database(self, db):
        for table in TABLES:
            actual = {r[1] for r in db.execute(f"PRAGMA table_info({table.name})")}
            assert table.column_names() == actual, f"{table.name} drifted from its declaration"

    def test_migrate_is_idempotent(self, db):
        result = migrate(db)
        assert result["created"] == []
        assert result["added"] == []


class TestAdditiveOnly:
    def test_new_column_is_added_to_an_existing_table(self, tmp_path):
        conn = connect(tmp_path / "t.db")
        # The index must go first — SQLite refuses to drop a column an index
        # still references.
        conn.execute("DROP INDEX idx_tasks_parent")
        conn.execute("ALTER TABLE tasks DROP COLUMN parent")
        conn.commit()
        result = migrate(conn)
        assert "tasks.parent" in result["added"]
        assert {r[1] for r in conn.execute("PRAGMA table_info(tasks)")} >= {"parent"}
        conn.close()

    def test_unknown_column_is_reported_not_dropped(self, tmp_path):
        conn = connect(tmp_path / "t.db")
        conn.execute("ALTER TABLE tasks ADD COLUMN legacy_field TEXT")
        conn.commit()
        result = migrate(conn)
        assert "tasks.legacy_field" in result["orphaned"]
        # The point of the requirement: it survives the migration.
        assert "legacy_field" in {r[1] for r in conn.execute("PRAGMA table_info(tasks)")}
        conn.close()

    def test_migrate_emits_no_destructive_sql(self, tmp_path):
        """Structural guard — migrate() must only ever CREATE or ADD COLUMN.

        Uses set_trace_callback rather than patching Connection.execute, which
        is read-only on sqlite3.Connection. The callback sees every statement
        the connection actually runs, which is a stronger check anyway.
        """
        conn = connect(tmp_path / "t.db")
        conn.execute("ALTER TABLE tasks ADD COLUMN legacy_field TEXT")
        conn.commit()

        statements: list[str] = []
        conn.set_trace_callback(statements.append)
        migrate(conn)
        conn.set_trace_callback(None)

        assert statements, "trace callback captured nothing — the guard would vacuously pass"

        for sql in statements:
            upper = sql.upper()
            assert "DROP" not in upper, f"destructive statement emitted: {sql}"
            assert "DELETE" not in upper, f"destructive statement emitted: {sql}"
            if "ALTER TABLE" in upper:
                assert "ADD COLUMN" in upper, f"non-additive ALTER emitted: {sql}"
        conn.close()


class TestConnectionPragmas:
    def test_wal_is_enabled(self, db):
        assert db.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"

    def test_busy_timeout_is_set(self, db):
        assert db.execute("PRAGMA busy_timeout").fetchone()[0] > 0

    def test_foreign_keys_are_enforced(self, db):
        assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1

    def test_foreign_key_violation_is_rejected(self, db):
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO task_events (task_id, kind, text) VALUES (?, ?, ?)",
                ("does-not-exist", "note", "orphan"),
            )
            db.commit()


class TestConstraints:
    def test_edges_are_idempotent(self, db):
        db.execute("INSERT INTO tasks (id, title) VALUES ('a', 'A')")
        db.execute("INSERT INTO tasks (id, title) VALUES ('b', 'B')")
        db.execute("INSERT INTO task_edges (from_id, to_id, rel) VALUES ('a','b','depends_on')")
        db.commit()
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO task_edges (from_id, to_id, rel) VALUES ('a','b','depends_on')")

    def test_commit_capture_is_idempotent(self, db):
        db.execute("INSERT INTO tasks (id, title) VALUES ('a', 'A')")
        db.execute("INSERT INTO task_commits (task_id, sha) VALUES ('a','deadbeef')")
        db.commit()
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO task_commits (task_id, sha) VALUES ('a','deadbeef')")


class TestTableDeclaration:
    def test_create_sql_includes_constraints(self):
        t = Table("x", (Column("id", "TEXT"),), ("PRIMARY KEY (id)",))
        assert "PRIMARY KEY (id)" in t.create_sql()
