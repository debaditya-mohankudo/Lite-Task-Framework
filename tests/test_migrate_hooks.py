"""Migration tests — the claims the migration makes about itself.

Every assertion here corresponds to something the migration asserts in prose.
The idempotency test exists because the prose was WRONG on the first live run:
tasks upsert by id and edges/commits have natural keys, but task_events has an
autoincrement id and nothing to collide with, so a second run duplicated all
4072 rows into a real database. Nothing caught it except running the migration
twice and counting.

That is the bargain the rest of this repo already makes — a claim nobody runs
is a comment.
"""
from __future__ import annotations

import sqlite3

import pytest

from taskfw.db import connect
from taskfw.migrate_hooks import migrate

SOURCE_DDL = """
CREATE TABLE open_tasks (
    id TEXT PRIMARY KEY, title TEXT, body TEXT, tags TEXT, status TEXT,
    created_at TIMESTAMP, updated_at TIMESTAMP, issue_type TEXT,
    parent_id TEXT, document TEXT
);
CREATE TABLE task_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, prompt_id TEXT,
    session_id TEXT, turn INTEGER, summary TEXT, tools TEXT,
    logged_at TIMESTAMP, related TEXT, memories TEXT
);
CREATE TABLE task_edges (
    from_id TEXT, to_id TEXT, relation_type TEXT, created_at TIMESTAMP
);
CREATE TABLE commit_task_map (
    id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, commit_hash TEXT,
    repo_path TEXT, logged_at TIMESTAMP
);
"""


def _task(id, issue_type="task", status="open", parent=None, title=None,
          body="", tags="", document=""):
    return (id, title or f"task {id}", body, tags, status,
            "2026-01-01 00:00:00", "2026-01-01 00:00:00", issue_type,
            parent, document)


@pytest.fixture
def source(tmp_path):
    """A claude-hooks store with one row of every shape the mapping handles."""
    path = tmp_path / "proj_tasks.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(SOURCE_DDL)
    conn.executemany(
        "INSERT INTO open_tasks VALUES (?,?,?,?,?,?,?,?,?,?)",
        [
            _task("epic01", "epic"),
            _task("story1", "story", parent="epic01"),
            _task("bug001", "bug", status="done"),
            _task("sub001", "subtask", parent="story1"),
            _task("feed01", "feedback"),
            _task("revw01", "task", status="review"),
            # An epic with a parent — impossible in taskfw.
            _task("epic02", "epic", parent="epic01"),
            # Child listed BEFORE its parent, to prove ordering is computed.
            _task("child1", "task", parent="latepa"),
            _task("latepa", "task"),
            _task("check1", "task", body="- [x] done bit\n- [ ] open bit\n"),
        ],
    )
    conn.executemany(
        "INSERT INTO task_events (task_id, summary, logged_at) VALUES (?,?,?)",
        [
            ("epic01", "did a thing", "2026-01-01 01:00:00"),
            ("epic01", "", "2026-01-01 02:00:00"),          # empty — skipped
            ("ghost1", "orphan event", "2026-01-01 03:00:00"),  # unknown task
        ],
    )
    conn.executemany(
        "INSERT INTO task_edges VALUES (?,?,?,?)",
        [("story1", "bug001", "blocks", "2026-01-01 00:00:00"),
         ("story1", "ghost1", "blocks", "2026-01-01 00:00:00")],  # orphan
    )
    conn.executemany(
        "INSERT INTO commit_task_map (task_id, commit_hash, repo_path, logged_at) "
        "VALUES (?,?,?,?)",
        [("bug001", "abc123", "/repo", "2026-01-01 00:00:00"),
         ("ghost1", "def456", "/repo", "2026-01-01 00:00:00")],  # orphan
    )
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def dest(tmp_path):
    return tmp_path / "tasks.db"


def counts(dest):
    conn = connect(dest)
    return {
        t: conn.execute(f"SELECT count(*) AS n FROM {t}").fetchone()["n"]
        for t in ("tasks", "task_events", "task_edges", "task_commits")
    }


def rows(dest):
    conn = connect(dest)
    return {r["id"]: r for r in conn.execute("SELECT * FROM tasks")}


class TestIdempotency:
    def test_running_twice_changes_nothing(self, source, dest):
        """THE regression. A second run duplicated 4072 event rows in a real
        database, because task_events has no natural key to collide with."""
        migrate(source, dest=dest)
        first = counts(dest)
        migrate(source, dest=dest)
        assert counts(dest) == first

    def test_three_runs_are_still_stable(self, source, dest):
        migrate(source, dest=dest)
        migrate(source, dest=dest)
        second = counts(dest)
        migrate(source, dest=dest)
        assert counts(dest) == second


class TestTypeCollapse:
    def test_epic_stays_an_epic(self, source, dest):
        migrate(source, dest=dest)
        assert rows(dest)["epic01"]["epic"] == 1

    @pytest.mark.parametrize("task_id", ["story1", "bug001", "sub001", "feed01"])
    def test_other_types_become_tasks(self, source, dest, task_id):
        migrate(source, dest=dest)
        assert rows(dest)[task_id]["epic"] == 0

    @pytest.mark.parametrize("task_id,original",
                             [("story1", "story"), ("bug001", "bug"),
                              ("sub001", "subtask"), ("feed01", "feedback")])
    def test_the_original_type_survives_as_a_tag(self, source, dest, task_id, original):
        """The label is what was dropped, so the label is what is preserved."""
        migrate(source, dest=dest)
        assert original in rows(dest)[task_id]["data"]

    def test_an_epic_with_a_parent_is_demoted_not_orphaned(self, source, dest):
        """Demoting discards a label; dropping the parent would discard a real
        relationship."""
        migrate(source, dest=dest)
        row = rows(dest)["epic02"]
        assert row["epic"] == 0
        assert row["parent"] == "epic01"
        assert "was-epic" in row["data"]


class TestStatus:
    def test_review_becomes_open_and_says_so(self, source, dest):
        migrate(source, dest=dest)
        row = rows(dest)["revw01"]
        assert row["status"] == "open"
        assert "was-review" in row["data"]

    def test_known_statuses_pass_through(self, source, dest):
        migrate(source, dest=dest)
        assert rows(dest)["bug001"]["status"] == "done"


class TestOrdering:
    def test_a_child_listed_before_its_parent_still_migrates(self, source, dest):
        """Source order is not dependency order, so the migration computes one."""
        migrate(source, dest=dest)
        assert rows(dest)["child1"]["parent"] == "latepa"

    def test_a_parent_cycle_is_reported_not_reparented(self, tmp_path, dest):
        path = tmp_path / "cyclic.db"
        conn = sqlite3.connect(str(path))
        conn.executescript(SOURCE_DDL)
        conn.executemany(
            "INSERT INTO open_tasks VALUES (?,?,?,?,?,?,?,?,?,?)",
            [_task("aaa", parent="bbb"), _task("bbb", parent="aaa")],
        )
        conn.commit()
        conn.close()

        report = migrate(path, dest=dest)
        assert sorted(report.unplaceable) == ["aaa", "bbb"]
        assert report.tasks == 0


class TestAccounting:
    def test_every_source_row_is_accounted_for(self, source, dest):
        """Migrated + empty + orphaned must equal what was there. A dropped row
        nothing reports is indistinguishable from one that never existed."""
        report = migrate(source, dest=dest)
        assert report.events + report.events_empty + report.events_orphaned == 3
        assert report.edges + report.edges_orphaned == 2
        assert report.commits + report.commits_orphaned == 2

    def test_orphans_are_counted_not_silently_dropped(self, source, dest):
        report = migrate(source, dest=dest)
        assert report.events_orphaned == 1
        assert report.edges_orphaned == 1
        assert report.commits_orphaned == 1

    def test_empty_events_are_distinguished_from_orphans(self, source, dest):
        report = migrate(source, dest=dest)
        assert report.events_empty == 1
        assert report.events == 1


class TestDryRun:
    def test_dry_run_counts_without_writing(self, source, dest):
        report = migrate(source, dest=dest, dry_run=True)
        assert report.tasks == 10
        assert counts(dest) == {"tasks": 0, "task_events": 0,
                                "task_edges": 0, "task_commits": 0}


class TestContent:
    def test_body_checklists_become_resolution_items(self, source, dest):
        """claude-hooks tracked resolution as prose; taskfw has a typed list, so
        the items are recovered rather than left where nothing can count them."""
        migrate(source, dest=dest)
        conn = connect(dest)
        from taskfw.store import TaskStore
        task = TaskStore(conn=conn).get("check1")
        assert [i.text for i in task.resolution] == ["done bit", "open bit"]
        assert [i.done for i in task.resolution] == [True, False]

    def test_body_is_preserved_verbatim_in_notes(self, source, dest):
        migrate(source, dest=dest)
        from taskfw.store import TaskStore
        task = TaskStore(conn=connect(dest)).get("check1")
        assert "- [x] done bit" in task.notes

    def test_grooming_moves_across_when_present(self, tmp_path, dest):
        path = tmp_path / "groomed.db"
        conn = sqlite3.connect(str(path))
        conn.executescript(SOURCE_DDL)
        conn.execute(
            "INSERT INTO open_tasks VALUES (?,?,?,?,?,?,?,?,?,?)",
            _task("grm001", document='{"grooming": {"risks": [{"text": "r"}],'
                                     ' "clarifications": []},'
                                     ' "introspection": {"reports": [{"outcome": "done"}]}}'),
        )
        conn.commit()
        conn.close()

        migrate(path, dest=dest)
        from taskfw.store import TaskStore
        task = TaskStore(conn=connect(dest)).get("grm001")
        assert task.grooming["risks"] == [{"text": "r"}]
        # Empty fields are dropped rather than carried as noise.
        assert "clarifications" not in task.grooming
        assert task.introspection == [{"outcome": "done"}]

    def test_unparseable_document_is_skipped_not_guessed(self, tmp_path, dest):
        path = tmp_path / "bad.db"
        conn = sqlite3.connect(str(path))
        conn.executescript(SOURCE_DDL)
        conn.execute("INSERT INTO open_tasks VALUES (?,?,?,?,?,?,?,?,?,?)",
                     _task("bad001", document="{not json"))
        conn.commit()
        conn.close()

        migrate(path, dest=dest)
        from taskfw.store import TaskStore
        assert TaskStore(conn=connect(dest)).get("bad001").grooming == {}
