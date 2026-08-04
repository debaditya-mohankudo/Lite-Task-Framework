"""Logging tests — the debuggability contract, asserted rather than assumed.

The standard these pin: a refusal and a state change must each be explicable
from one log line, including the ids involved. Logging that is not tested
quietly rots, and it rots invisibly because nothing fails.
"""
from __future__ import annotations

import json
import logging

import pytest

from taskfw.lifecycle import check_save, check_transition
from taskfw.models import ResolutionItem, Task
from taskfw.store import TaskStore


@pytest.fixture
def store(tmp_path):
    s = TaskStore(tmp_path / "t.db")
    yield s
    s.close()


class TestRuleLogging:
    def test_denial_logs_at_info_with_rule_and_reason(self, caplog):
        with caplog.at_level(logging.INFO, logger="taskfw"):
            check_transition("done", "open")
        # getMessage() interpolates args; .message only exists once a Formatter
        # has run, which is not guaranteed for captured records.
        rendered = [r.getMessage() for r in caplog.records]
        assert any("DENY" in m and "transition" in m for m in rendered), rendered
        assert any("terminal" in m for m in rendered), rendered

    def test_denial_line_names_the_states_involved(self, caplog):
        with caplog.at_level(logging.INFO, logger="taskfw"):
            check_transition("blocked", "done")
        text = caplog.text
        assert "blocked" in text and "done" in text

    def test_allow_is_debug_not_info(self, caplog):
        with caplog.at_level(logging.INFO, logger="taskfw"):
            check_transition("open", "done")
        assert "ALLOW" not in caplog.text

    def test_allow_is_visible_at_debug(self, caplog):
        with caplog.at_level(logging.DEBUG, logger="taskfw"):
            check_transition("open", "done")
        assert "ALLOW" in caplog.text

    def test_self_parent_denial_names_the_task(self, caplog):
        t = Task(title="t")
        t.parent = t.id
        with caplog.at_level(logging.INFO, logger="taskfw"):
            check_save(t)
        assert t.id in caplog.text


class TestStoreLogging:
    def test_create_is_logged_with_id(self, store, caplog):
        with caplog.at_level(logging.INFO, logger="taskfw"):
            t = store.save(Task(title="new one"))
        assert "CREATE" in caplog.text and t.id in caplog.text

    def test_status_change_logs_both_states(self, store, caplog):
        t = store.save(Task(title="t"))
        t.status = "done"
        with caplog.at_level(logging.INFO, logger="taskfw"):
            caplog.clear()
            store.save(t)
        assert "STATUS" in caplog.text
        assert "open" in caplog.text and "done" in caplog.text

    def test_plain_update_is_debug_only(self, store, caplog):
        t = store.save(Task(title="t"))
        t.resolution = [ResolutionItem("a")]
        with caplog.at_level(logging.INFO, logger="taskfw"):
            # caplog accumulates for the whole test — at_level sets the level
            # but does not scope capture, so the setup save above would
            # otherwise leak its CREATE line into this assertion.
            caplog.clear()
            store.save(t)
        assert "STATUS" not in caplog.text
        assert "CREATE" not in caplog.text

    def test_link_and_unlink_are_logged(self, store, caplog):
        a, b = store.save(Task(title="a")), store.save(Task(title="b"))
        with caplog.at_level(logging.INFO, logger="taskfw"):
            store.link(a.id, b.id, "depends_on")
            store.unlink(a.id, b.id)
        assert "link" in caplog.text and "unlink" in caplog.text
        assert "removed=1" in caplog.text

    def test_duplicate_link_is_distinguishable_in_the_log(self, store, caplog):
        a, b = store.save(Task(title="a")), store.save(Task(title="b"))
        store.link(a.id, b.id)
        with caplog.at_level(logging.INFO, logger="taskfw"):
            caplog.clear()
            store.link(a.id, b.id)
        assert "already existed" in caplog.text

    def test_commit_capture_is_logged(self, store, caplog):
        t = store.save(Task(title="t"))
        with caplog.at_level(logging.INFO, logger="taskfw"):
            store.add_commit(t.id, "abcdef1234567890", "/repo")
        assert "commit" in caplog.text and t.id in caplog.text

    def test_active_task_changes_are_logged(self, store, caplog):
        t = store.save(Task(title="t"))
        with caplog.at_level(logging.INFO, logger="taskfw"):
            store.set_active(t.id, scope="/w")
            store.clear_active(scope="/w")
        assert "active" in caplog.text and "/w" in caplog.text


class TestLogDestination:
    def test_taskfw_logger_never_writes_to_stdout(self):
        """An MCP server speaks JSON-RPC on stdout; a stray write corrupts it."""
        import sys

        from taskfw.log import get_logger

        get_logger("probe")
        for handler in logging.getLogger("taskfw").handlers:
            stream = getattr(handler, "stream", None)
            assert stream is not sys.stdout


class TestSQLiteSink:
    """Exercised directly, bypassing the global logger — a test run's global
    logger uses JsonlHandler (see TestJsonlSink), never SQLiteHandler, so
    round-tripping through get_logger() itself would test nothing here.
    """

    def test_emit_never_raises_on_a_broken_connection(self):
        """A broken log write must not break the call that triggered it."""
        from taskfw.log import SQLiteHandler

        handler = SQLiteHandler()
        handler._local.conn = object()  # anything with no .execute() fails predictably
        record = logging.LogRecord("taskfw.probe", logging.INFO, __file__, 1, "boom", None, None)
        handler.emit(record)  # must not raise

    def test_emit_from_a_second_thread_still_lands(self):
        """Regression for task:7fff42f4: a single process-wide connection,

        reused across the worker threads an MCP server dispatches sync tool
        calls onto, eventually gets used from a thread that didn't create
        it — sqlite3 raises ProgrammingError on that, which the bare except
        in emit() swallowed silently for the rest of the process's life.
        One connection per thread (threading.local) makes that reuse
        unrepresentable instead of catching it after the fact.
        """
        import threading
        import uuid

        from taskfw import mcp_server as m
        from taskfw.log import SQLiteHandler

        handler = SQLiteHandler()
        marker = f"probe-{uuid.uuid4().hex}"
        record = logging.LogRecord("taskfw.thread_probe", logging.INFO, __file__, 1, marker, None, None)
        handler.emit(record)  # opens the handler's connection on this thread

        second_marker = f"probe-{uuid.uuid4().hex}"
        second_record = logging.LogRecord(
            "taskfw.thread_probe", logging.INFO, __file__, 1, second_marker, None, None
        )
        t = threading.Thread(target=handler.emit, args=(second_record,))
        t.start()
        t.join()

        result = m.tasks__logs(limit=500)
        messages = [row["message"] for row in result["logs"]]
        assert any(marker in msg for msg in messages)
        assert any(second_marker in msg for msg in messages)

    def test_a_log_call_round_trips_through_tasks__logs(self):
        """SQLiteHandler() and tasks__logs both default to config.db_path() —
        the isolated test-session path tests/conftest.py points TASKFW_DB at.
        """
        import uuid

        from taskfw import mcp_server as m
        from taskfw.log import SQLiteHandler

        marker = f"probe-{uuid.uuid4().hex}"
        record = logging.LogRecord("taskfw.roundtrip_probe", logging.INFO, __file__, 1, marker, None, None)
        SQLiteHandler().emit(record)

        result = m.tasks__logs(limit=500)
        assert any(marker in row["message"] for row in result["logs"])


class TestJsonlSink:
    def test_emit_writes_one_json_line_per_record(self, tmp_path):
        from taskfw.log import JsonlHandler

        path = tmp_path / "log.jsonl"
        handler = JsonlHandler(str(path))
        record = logging.LogRecord("taskfw.probe", logging.INFO, __file__, 1, "hello", None, None)
        handler.emit(record)

        lines = path.read_text().splitlines()
        assert len(lines) == 1
        row = json.loads(lines[0])
        assert row["message"] == "hello" and row["logger"] == "taskfw.probe"

    def test_construction_truncates_the_file(self, tmp_path):
        from taskfw.log import JsonlHandler

        path = tmp_path / "log.jsonl"
        path.write_text("stale content from a previous run\n")
        JsonlHandler(str(path))
        assert path.read_text() == ""

    def test_emit_never_raises_if_the_path_becomes_unwritable(self, tmp_path):
        from taskfw.log import JsonlHandler

        handler = JsonlHandler(str(tmp_path / "log.jsonl"))
        handler._path = "/nonexistent-dir/x/log.jsonl"  # simulate failure after construction
        record = logging.LogRecord("taskfw.probe", logging.INFO, __file__, 1, "x", None, None)
        handler.emit(record)  # must not raise

    def test_a_real_log_call_lands_in_the_test_session_jsonl_file(self):
        """The global logger, unmodified — this IS what test runs actually use."""
        import os
        import uuid

        from taskfw.log import get_logger

        marker = f"probe-{uuid.uuid4().hex}"
        get_logger("jsonl_probe").info(marker)

        path = os.environ["TASKFW_LOG_JSONL"]
        with open(path) as f:
            assert any(marker in line for line in f)
