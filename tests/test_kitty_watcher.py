import os
import sqlite3
import sys
import time
from types import SimpleNamespace

import pytest

from taskfw.kitty import watcher
from taskfw.kitty.store import Store

BOSS = SimpleNamespace(listening_on="unix:/tmp/s")


@pytest.fixture(autouse=True)
def errors_log(tmp_path, monkeypatch):
    """Keep failure traces out of the real ~/.kitty-taskfw while the suite deliberately breaks things."""
    path = tmp_path / "logs" / "watcher-errors.log"
    monkeypatch.setattr(watcher, "DEFAULT_ERRORS", str(path))
    return path


def win(store, wid=5, uv=None, env=None):
    e = {"KITTY_TASKFW_PYTHON": sys.executable, **({"KITTY_TASKFW_STORE": store} if store else {}), **(env or {})}
    return SimpleNamespace(id=wid, user_vars={"task_id": "abc"} if uv is None else uv,
                           child=SimpleNamespace(environ=e))


def test_each_callback_appends_one_event(tmp_path, monkeypatch):
    monkeypatch.setenv("KITTY_TASKFW_STORE", "/nonexistent/decoy.sqlite")  # kitty's own environ must not pick the store
    st = Store(tmp_path / "s.sqlite")
    w = win(st.path)
    calls = [(watcher.on_focus_change, {"focused": True}),
             (watcher.on_set_user_var, {"key": "state", "value": "waiting"}),
             (watcher.on_cmd_startstop, {"is_start": True, "cmdline": "pytest", "time": 1}),
             (watcher.on_cmd_startstop, {"is_start": False, "cmdline": "pytest", "time": 2}),
             (watcher.on_close, {})]
    for fn, data in calls:
        fn(BOSS, w, data)
    # writers run concurrently, so wait for all five and compare kinds as a set
    for _ in range(100):
        if len(st.events("abc")) == 5:
            break
        time.sleep(0.05)
    rows = st.events("abc")
    assert sorted(r["kind"] for r in rows) == sorted(["focus", "set_user_var", "cmd_start", "cmd_stop", "close"])
    assert all(r["socket"] == "unix:/tmp/s" and r["window_id"] == 5 for r in rows)


def test_window_without_marker_spawns_nothing(tmp_path):
    assert watcher.record(BOSS, win(str(tmp_path / "never.sqlite"), uv={}), "close", {}) is None
    assert not (tmp_path / "never.sqlite").exists()


def test_env_marker_is_enough(tmp_path):
    st = Store(tmp_path / "s.sqlite")
    p = watcher.record(BOSS, win(st.path, uv={}, env={"TASKFW_TASK_ID": "envtask"}), "close", {})
    p.wait(10)
    assert [r["task_id"] for r in st.events()] == ["envtask"]


def test_garbage_never_raises():
    bad = SimpleNamespace(id="x", user_vars=None, child=None)
    assert watcher.record(None, bad, "close", {}) is None
    assert watcher.record(BOSS, win(None, env={"KITTY_TASKFW_PYTHON": "/no/such/python"}), "close", {}) is None


def test_locked_db_cannot_block_the_callback(tmp_path):
    st = Store(tmp_path / "s.sqlite")
    lock = sqlite3.connect(st.path, isolation_level=None)
    lock.execute("BEGIN EXCLUSIVE")
    try:
        t0 = time.monotonic()
        p = watcher.record(BOSS, win(st.path), "close", {})
        elapsed = time.monotonic() - t0
        assert elapsed < 0.5  # returned at once; the writer, not kitty, waits on the lock
        assert p.poll() is None
    finally:
        lock.execute("ROLLBACK"); lock.close()
    p.wait(10)
    assert [r["kind"] for r in st.events()] == ["close"]  # the event was not lost, just deferred


def _run(store, **kw):
    p = watcher.record(BOSS, win(store, **kw), "focus", {"focused": True})
    if p is not None:
        p.wait(10)
    return p


def test_missing_table_leaves_a_trace_naming_the_event(tmp_path, errors_log):
    bare = tmp_path / "bare.sqlite"
    sqlite3.connect(bare).close()  # a store with no events table
    _run(str(bare))
    line = errors_log.read_text()
    assert "kind=focus" in line and "window=5" in line and "task=abc" in line
    assert "OperationalError" in line and "no such table" in line


def test_store_in_missing_directory_still_leaves_a_trace(tmp_path, errors_log):
    _run(str(tmp_path / "nope" / "deeper" / "s.sqlite"))  # trace is independent of the store path
    assert "OperationalError" in errors_log.read_text()


def test_interpreter_that_cannot_start_leaves_a_trace_and_returns_none(tmp_path, errors_log):
    assert _run(str(tmp_path / "s.sqlite"), env={"KITTY_TASKFW_PYTHON": "/no/such/python"}) is None
    line = errors_log.read_text()
    assert "writer not started" in line and "kind=focus" in line and "task=abc" in line


def test_interpreter_without_sqlite3_leaves_a_trace(tmp_path, errors_log):
    shim = tmp_path / "py"  # a python that cannot import sqlite3, like kitty's bundled one
    shim.write_text("#!/bin/bash\n"
                    f"exec {sys.executable} -c 'import sys; sys.modules[\"sqlite3\"] = None; "
                    "src = sys.argv[1]; sys.argv = sys.argv[1:]; exec(src)' \"$2\" \"${@:3}\"\n")
    shim.chmod(0o755)
    _run(str(tmp_path / "s.sqlite"), env={"KITTY_TASKFW_PYTHON": str(shim)})
    assert "kind=focus" in errors_log.read_text()


def test_unwritable_log_never_drops_the_event_or_raises(tmp_path, monkeypatch):
    blocker = tmp_path / "file"
    blocker.write_text("x")  # a file where the log directory should be
    monkeypatch.setattr(watcher, "DEFAULT_ERRORS", str(blocker / "sub" / "e.log"))
    st = Store(tmp_path / "s.sqlite")
    _run(st.path)
    assert [r["kind"] for r in st.events()] == ["focus"]
    assert _run(str(tmp_path / "nope" / "s.sqlite")) is not None  # failing write, unusable log: still quiet


def test_trace_is_bounded_and_keeps_the_newest_failure(tmp_path, errors_log, monkeypatch):
    monkeypatch.setattr(watcher, "MAX_LOG", 400)
    bad = str(tmp_path / "nope" / "s.sqlite")
    for i in range(30):
        w = win(bad)
        w.id = 1000 + i
        watcher.record(BOSS, w, "focus", {}).wait(10)
    size = errors_log.stat().st_size + os.path.getsize(str(errors_log) + ".1")
    assert size < 4 * 400 + 1000  # cap plus rotation, not 30 lines' worth
    assert "window=1029" in errors_log.read_text() + open(str(errors_log) + ".1").read()


def test_record_does_not_leak_file_descriptors(tmp_path):
    st = Store(tmp_path / "s.sqlite")
    fds = lambda: len(os.listdir("/dev/fd"))
    _run(st.path)
    before = fds()
    for _ in range(25):
        watcher.record(BOSS, win(st.path), "focus", {}).wait(10)
    assert fds() <= before + 1


def test_failure_paths_do_not_slow_the_callback(tmp_path):
    t0 = time.monotonic()
    for _ in range(20):
        watcher.record(BOSS, win(str(tmp_path / "nope" / "s.sqlite")), "focus", {})
    assert (time.monotonic() - t0) / 20 < 0.05
