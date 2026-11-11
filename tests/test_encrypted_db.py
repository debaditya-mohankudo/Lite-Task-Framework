"""tasks.db may be SQLCipher-encrypted (task:8b0561d1); the stores must not notice.

The opener, key lookup and adapter live in cli-graph's mac/encdb.py and are
loaded by path (taskfw/db/encrypted.py). These tests use a throwaway key file,
never the real one, and skip when apsw or that file is not available.
"""
from __future__ import annotations

import sqlite3

import pytest

apsw = pytest.importorskip("apsw")

from taskfw.db import encrypted
from taskfw.memory import MemoryStore
from taskfw.store import TaskStore
from taskfw.task import Task

KEY = "cd" * 32


@pytest.fixture
def enc_path(tmp_path, monkeypatch):
    try:
        mod = encrypted._encdb()
    except encrypted.EncryptedDBError as e:
        pytest.skip(str(e))
    keyfile = tmp_path / "memory.key"
    keyfile.write_text(KEY + "\n")
    monkeypatch.setattr(mod, "KEY_FILE", keyfile)
    mod._key_cache.clear()

    plain = tmp_path / "plain.db"
    store = TaskStore(plain)
    tid = store.save(Task(title="Encrypt the task store")).id
    store.close()
    src = apsw.Connection(str(plain), flags=apsw.SQLITE_OPEN_READWRITE | apsw.SQLITE_OPEN_URI)
    out = tmp_path / "tasks.db"
    src.execute("VACUUM INTO ?", (f"{out.as_uri()}?cipher=sqlcipher&hexkey={KEY}",))
    src.close()
    assert encrypted.is_encrypted(out)
    yield out, tid
    mod._key_cache.clear()


def test_plain_file_is_not_encrypted(tmp_path):
    p = tmp_path / "p.db"
    TaskStore(p).close()
    assert not encrypted.is_encrypted(p)
    assert not encrypted.is_encrypted(tmp_path / "missing.db")


def test_store_reads_and_writes_an_encrypted_file(enc_path):
    path, tid = enc_path
    with pytest.raises(sqlite3.DatabaseError):
        sqlite3.connect(path).execute("select * from tasks")
    store = TaskStore(path)
    assert store.get(tid).title == "Encrypt the task store"
    new = store.save(Task(title="Written through the adapter")).id
    store.close()
    again = TaskStore(path)
    assert again.get(new).title == "Written through the adapter"
    again.close()


def test_search_and_memory_work_on_an_encrypted_file(enc_path):
    path, tid = enc_path
    store = TaskStore(path)
    assert [t.id for t in store.search("encrypt")] == [tid]
    # a malformed FTS5 query must still fall back to LIKE, not raise apsw's error
    store.search('"unbalanced')
    mem = MemoryStore(conn=store.conn)
    rec = mem.record("encrypted-memory", "Memories are written on the encrypted connection too.", tid)
    assert rec["learned_from"] == [tid]
    assert mem.recall("encrypted")
    store.close()


def test_wrong_key_fails_with_a_clear_error(enc_path, tmp_path):
    path, _ = enc_path
    mod = encrypted._encdb()
    mod._key_cache.clear()
    (tmp_path / "memory.key").write_text("ef" * 32 + "\n")
    with pytest.raises(encrypted.EncryptedDBError, match="wrong key"):
        TaskStore(path)
