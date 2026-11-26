"""Loop memory in a shared memory DB (TASKFW_MEMORY_DB).

The DDL below stands in for the owner's schema, cut down to what taskfw
touches plus the columns whose defaults matter to an insert. taskfw must work
against it without creating or altering anything.
"""
from __future__ import annotations

import sqlite3

import pytest

from taskfw import config, mcp_server, memory_shared
from taskfw.context import TaskContext
from taskfw.dispatcher import introspection_nudge
from taskfw.memory import MemoryStore, Rejected, open_memory
from taskfw.memory_shared import SharedMemoryStore, SharedSchemaError
from taskfw.store import TaskStore
from taskfw.task import Task

OWNER_DDL = """
CREATE TABLE memories (
    id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, type TEXT NOT NULL,
    domain TEXT NOT NULL DEFAULT 'global', tags TEXT, body TEXT NOT NULL,
    updated TIMESTAMP, hit_count INTEGER DEFAULT 0, last_hit TIMESTAMP,
    origin TEXT NOT NULL DEFAULT 'general', kind TEXT DEFAULT '',
    scope TEXT DEFAULT '', created TIMESTAMP);
CREATE TABLE memory_links (src_id INTEGER, dst_id INTEGER, type TEXT,
    created TIMESTAMP DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (src_id, dst_id, type));
CREATE TABLE memory_task_links (memory_id INTEGER, task_id TEXT,
    relation TEXT, created TIMESTAMP, PRIMARY KEY (memory_id, task_id, relation));
CREATE TRIGGER c1 BEFORE DELETE ON memories BEGIN
    DELETE FROM memory_links WHERE src_id = OLD.id OR dst_id = OLD.id; END;
CREATE TRIGGER c2 BEFORE DELETE ON memories BEGIN
    DELETE FROM memory_task_links WHERE memory_id = OLD.id; END;
"""

LESSON = "FTS5 is a compile-time option, so search must degrade to LIKE rather than fail."
OTHER = "Deriving a value on read removes the reconciliation logic entirely."


def _query(path, sql, *params):
    con = sqlite3.connect(path)
    try:
        rows = con.execute(sql, params).fetchall()
        con.commit()
        return rows
    finally:
        con.close()


@pytest.fixture
def shared(tmp_path):
    path = tmp_path / "MEMORY.sqlite"
    con = sqlite3.connect(path)
    con.executescript(OWNER_DDL)
    con.execute("INSERT INTO memories (name, type, body) VALUES "
                "('general-note', 'feedback', 'FTS5 search note kept by another tool')")
    con.commit()
    con.close()
    return path


@pytest.fixture
def store(shared):
    s = SharedMemoryStore(shared)
    yield s
    s.close()


@pytest.fixture
def tasks(tmp_path):
    t = TaskStore(tmp_path / "t.db")
    yield t
    t.close()


def test_unset_keeps_the_task_database(tasks):
    m = open_memory(tasks.conn)
    assert type(m) is MemoryStore and m.conn is tasks.conn


def test_setting_selects_the_shared_store(shared, tasks, monkeypatch):
    monkeypatch.setattr(config, "MEMORY_DB", shared)
    assert isinstance(open_memory(tasks.conn), SharedMemoryStore)


def test_record_get_link_and_standing(store, shared):
    first = store.record("degrade-fts", LESSON, "t1", "technique")
    assert first["learned_from"] == ["t1"] and first["standing"] == "unverified"
    again = store.record("degrade-fts", OTHER, "t2", "pitfall")
    assert (again["text"], again["kind"], again["learned_from"]) == (OTHER, "pitfall", ["t1", "t2"])
    assert again["created_at"] == first["created_at"]
    assert store.link("degrade-fts", "t3", "confirmed_by") is True
    assert store.link("degrade-fts", "t3", "confirmed_by") is False
    assert store.get("degrade-fts")["standing"] == "confirmed"
    assert _query(shared, "SELECT origin, domain FROM memories WHERE name='degrade-fts'") == [
        ("loop", "global")]


def test_general_memories_are_invisible_and_protected(store, shared):
    store.record("loop-one", LESSON, "t1")
    assert store.get("general-note") is None
    assert [m["slug"] for m in store.recall("search")] == ["loop-one"]
    with pytest.raises(Rejected, match="general memory"):
        store.record("general-note", OTHER, "t1")
    assert store.forget("general-note") is False
    assert _query(shared, "SELECT body FROM memories WHERE name='general-note'") == [
        ("FTS5 search note kept by another tool",)]
    assert _query(shared, "SELECT count(*) FROM memory_task_links") == [(1,)]


def test_supersede_here_or_by_another_tool(store, shared):
    for slug in ("old-one", "new-one", "newer-one"):
        store.record(slug, LESSON, "t1")
    store.supersede("old-one", "new-one")
    assert store.supersede("old-one", "newer-one")["superseded_by"] == "newer-one"
    assert "old-one" not in [m["slug"] for m in store.recall("compile-time")]
    assert "old-one" in [m["slug"] for m in store.recall("compile-time", include_superseded=True)]
    _query(shared, "INSERT INTO memory_links (src_id, dst_id, type) SELECT g.id, m.id, 'supersedes' "
                   "FROM memories g, memories m WHERE g.name='general-note' AND m.name='new-one'")
    assert store.get("new-one")["standing"] == "superseded"
    assert [m["slug"] for m in store.recall()] == ["newer-one"]


def test_recall_counts_hits_and_forget_cascades(store, shared):
    store.record("a-lesson", LESSON, "t1")
    assert store.recall("compile-time")[0]["hit_count"] == 1
    assert store.recall("compile-time", count_hit=False)[0]["hit_count"] == 1
    assert store.recall("zzz-nothing") == []
    assert store.forget("a-lesson") is True
    assert _query(shared, "SELECT count(*) FROM memory_task_links") == [(0,)]


def test_ranking_matches_the_task_database_store(store, tasks):
    local = MemoryStore(conn=tasks.conn)
    texts = {
        "alpha": "sqlite lock contention when two writers share a database file",
        "beta": "a database migration must stay additive so old readers keep working",
        "gamma": "the writer holds the lock; readers wait on the busy timeout",
        "delta": "name the test after the behaviour it pins, not the function",
    }
    for slug, text in texts.items():
        local.record(slug, text, "t1")
        store.record(slug, text, "t1")
    for query in ("database lock", "writer readers", "behaviour", "additive migration timeout"):
        want, got = [], []
        local.recall(query, trace=want)
        store.recall(query, trace=got)
        assert got == want, query


def test_bundle_lessons_mark_cross_project(store, tasks):
    elsewhere = tasks.save(Task(title="old work", scope="git:host/other"))
    here = tasks.save(Task(title="compile-time option search", scope="git:host/mine"))
    store.record("a-lesson", LESSON, elsewhere.id)
    lessons = TaskContext(tasks, memory=store).bundle(here.id)["lessons"]
    assert [(m["slug"], m.get("cross_project")) for m in lessons] == [("a-lesson", True)]


def test_introspection_nudge_reads_the_shared_citation(store):
    report = {"new_knowledge": ["a lesson"]}
    assert introspection_nudge(report, "t1", store)
    store.record("a-lesson", LESSON, "t1")
    assert introspection_nudge(report, "t1", store) is None


def test_set_store_follows_the_setting(shared, tasks, monkeypatch):
    monkeypatch.setattr(config, "MEMORY_DB", shared)
    mcp_server.set_store(tasks)
    try:
        assert isinstance(mcp_server.memory(), SharedMemoryStore)
    finally:
        mcp_server.set_store(None)


def test_leaves_the_schema_and_journal_mode_alone(shared):
    before = _query(shared, "SELECT type, name, sql FROM sqlite_master ORDER BY name")
    s = SharedMemoryStore(shared)
    s.record("a-lesson", LESSON, "t1")
    s.recall("compile-time")
    s.close()
    assert _query(shared, "SELECT type, name, sql FROM sqlite_master ORDER BY name") == before
    assert _query(shared, "PRAGMA journal_mode") == [("delete",)]


def test_missing_file_is_an_error_and_is_not_created(tmp_path):
    path = tmp_path / "absent.sqlite"
    with pytest.raises(SharedSchemaError, match="no database"):
        SharedMemoryStore(path)
    assert not path.exists()


def test_old_schema_is_a_clear_error(tmp_path):
    path = tmp_path / "old.sqlite"
    _query(path, "CREATE TABLE memories (id INTEGER PRIMARY KEY, name TEXT, type TEXT, body TEXT)")
    with pytest.raises(SharedSchemaError, match="memories.origin") as err:
        SharedMemoryStore(path)
    assert "table memory_task_links" in str(err.value)
    assert _query(path, "SELECT count(*) FROM sqlite_master") == [(1,)]


def test_open_waits_out_a_competing_writer(shared, monkeypatch):
    real, calls = memory_shared.open_any, []

    def flaky(path, **kwargs):
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError("database is locked")
        return real(path, **kwargs)

    monkeypatch.setattr(memory_shared, "open_any", flaky)
    SharedMemoryStore(shared).close()
    assert len(calls) == 3
