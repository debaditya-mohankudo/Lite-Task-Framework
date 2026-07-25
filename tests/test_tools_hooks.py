"""Tests for src/tools/hooks.py — server_memory, _decode, read_logs_sqlite, checkpoint_query."""
import json
import sqlite3
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import msgpack
import pytest

from src.tools.hooks import (
    _decode,
    handle_checkpoint_query,
    handle_read_logs_sqlite,
    handle_server_memory,
    handle_session_id,
)


# ---------------------------------------------------------------------------
# _decode
# ---------------------------------------------------------------------------

class TestDecode:
    def test_none_returns_none(self):
        assert _decode(None) is None

    def test_valid_msgpack(self):
        packed = msgpack.packb({"key": "val"}, use_bin_type=True)
        assert _decode(packed) == {"key": "val"}

    def test_fallback_to_utf8_on_bad_msgpack(self):
        assert _decode(b"plain text") == "plain text"

    def test_list_roundtrip(self):
        packed = msgpack.packb([1, 2, 3], use_bin_type=True)
        assert _decode(packed) == [1, 2, 3]


# ---------------------------------------------------------------------------
# handle_server_memory
# ---------------------------------------------------------------------------

def _mock_response(data: dict):
    body = json.dumps(data).encode()
    resp = MagicMock()
    resp.read.return_value = body
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


class TestHandleServerMemory:
    def test_returns_markdown_table(self):
        events = [
            {"type": "prompt", "content": "hello"},
            {"type": "tool", "content": "Read", "args": None},
        ]
        with patch("urllib.request.urlopen", return_value=_mock_response({"events": events})):
            result = handle_server_memory(n_events=10)
        assert "| # | Prompt | Tools |" in result
        assert "hello" in result
        assert "Read" in result

    def test_tool_with_path_args(self):
        home = Path.home()
        events = [
            {"type": "prompt", "content": "read file"},
            {"type": "tool", "content": "Read", "args": f"{home}/workspace/foo/bar/baz.py"},
        ]
        with patch("urllib.request.urlopen", return_value=_mock_response({"events": events})):
            result = handle_server_memory()
        assert "Read(" in result

    def test_tool_with_short_args(self):
        events = [
            {"type": "prompt", "content": "think"},
            {"type": "tool", "content": "Think", "args": "short arg"},
        ]
        with patch("urllib.request.urlopen", return_value=_mock_response({"events": events})):
            result = handle_server_memory()
        assert "Think(short arg)" in result

    def test_tool_without_preceding_prompt_is_ignored(self):
        events = [
            {"type": "tool", "content": "Orphan", "args": None},
        ]
        with patch("urllib.request.urlopen", return_value=_mock_response({"events": events})):
            result = handle_server_memory()
        # No prompt rows — table still renders with just headers
        assert "| # | Prompt | Tools |" in result

    def test_empty_events(self):
        with patch("urllib.request.urlopen", return_value=_mock_response({"events": []})):
            result = handle_server_memory()
        assert "| # | Prompt | Tools |" in result

    def test_unreachable_server_returns_error(self):
        with patch("urllib.request.urlopen", side_effect=OSError("refused")):
            result = handle_server_memory()
        assert "error" in result
        assert "unreachable" in result["error"]

    def test_prompt_truncated_at_60_chars(self):
        long_prompt = "x" * 80
        events = [{"type": "prompt", "content": long_prompt}]
        with patch("urllib.request.urlopen", return_value=_mock_response({"events": events})):
            result = handle_server_memory()
        assert "…" in result


# ---------------------------------------------------------------------------
# handle_read_logs_sqlite
# ---------------------------------------------------------------------------

def _make_logs_db(path: Path) -> None:
    with sqlite3.connect(str(path)) as conn:
        conn.execute(
            """CREATE TABLE hook_logs (
                id INTEGER PRIMARY KEY,
                ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                level TEXT,
                logger TEXT,
                message TEXT
            )"""
        )
        conn.executemany(
            "INSERT INTO hook_logs (level, logger, message) VALUES (?, ?, ?)",
            [
                ("INFO", "hooks.memory", "loaded 5 memories"),
                ("WARNING", "hooks.tasks", "task not found"),
                ("ERROR", "hooks.memory", "DB write failed"),
            ],
        )


class TestHandleReadLogsSqlite:
    def test_missing_db_returns_error(self, tmp_path):
        with patch("src.tools.hooks._HOOKS_LOG_DB", tmp_path / "missing.sqlite"):
            result = handle_read_logs_sqlite()
        assert "error" in result

    def test_returns_all_rows_by_default(self, tmp_path):
        db = tmp_path / "logs.sqlite"
        _make_logs_db(db)
        with patch("src.tools.hooks._HOOKS_LOG_DB", db):
            result = handle_read_logs_sqlite(format="json")
        assert result["count"] == 3

    def test_filter_by_level(self, tmp_path):
        db = tmp_path / "logs.sqlite"
        _make_logs_db(db)
        with patch("src.tools.hooks._HOOKS_LOG_DB", db):
            result = handle_read_logs_sqlite(level="ERROR", format="json")
        assert result["count"] == 1
        assert result["rows"][0]["level"] == "ERROR"

    def test_filter_by_logger(self, tmp_path):
        db = tmp_path / "logs.sqlite"
        _make_logs_db(db)
        with patch("src.tools.hooks._HOOKS_LOG_DB", db):
            result = handle_read_logs_sqlite(logger="tasks", format="json")
        assert result["count"] == 1

    def test_filter_by_search(self, tmp_path):
        db = tmp_path / "logs.sqlite"
        _make_logs_db(db)
        with patch("src.tools.hooks._HOOKS_LOG_DB", db):
            result = handle_read_logs_sqlite(search="memories", format="json")
        assert result["count"] == 1
        assert "memories" in result["rows"][0]["message"]

    def test_limit_capped_at_200(self, tmp_path):
        db = tmp_path / "logs.sqlite"
        _make_logs_db(db)
        with patch("src.tools.hooks._HOOKS_LOG_DB", db):
            result = handle_read_logs_sqlite(limit=9999, format="json")
        # limit is capped — all 3 rows still returned (< 200)
        assert result["count"] == 3

    def test_combined_filters(self, tmp_path):
        db = tmp_path / "logs.sqlite"
        _make_logs_db(db)
        with patch("src.tools.hooks._HOOKS_LOG_DB", db):
            result = handle_read_logs_sqlite(level="ERROR", logger="memory", format="json")
        assert result["count"] == 1
        assert "DB write failed" in result["rows"][0]["message"]

    def test_toon_is_default_format(self, tmp_path):
        db = tmp_path / "logs.sqlite"
        _make_logs_db(db)
        with patch("src.tools.hooks._HOOKS_LOG_DB", db):
            result = handle_read_logs_sqlite()
        assert isinstance(result, str)
        assert result.startswith("count: 3")
        assert "rows[3]{id,ts,level,logger,message}:" in result

    def test_toon_empty_rows(self, tmp_path):
        db = tmp_path / "logs.sqlite"
        _make_logs_db(db)
        with patch("src.tools.hooks._HOOKS_LOG_DB", db):
            result = handle_read_logs_sqlite(level="DEBUG")
        assert result == "count: 0\nrows[0]{}:"

    def test_toon_escapes_commas_and_quotes(self, tmp_path):
        db = tmp_path / "logs.sqlite"
        with sqlite3.connect(str(db)) as conn:
            conn.execute(
                """CREATE TABLE hook_logs (
                    id INTEGER PRIMARY KEY,
                    ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    level TEXT,
                    logger TEXT,
                    message TEXT
                )"""
            )
            conn.execute(
                "INSERT INTO hook_logs (level, logger, message) VALUES (?, ?, ?)",
                ("INFO", "hooks.memory", 'has, a comma and a "quote"'),
            )
        with patch("src.tools.hooks._HOOKS_LOG_DB", db):
            result = handle_read_logs_sqlite()
        assert '"has, a comma and a ""quote"""' in result


# ---------------------------------------------------------------------------
# handle_checkpoint_query
# ---------------------------------------------------------------------------

def _make_checkpoint_db(path: Path, with_memories: bool = True) -> None:
    with sqlite3.connect(str(path)) as conn:
        conn.executescript("""
            CREATE TABLE checkpoints (
                thread_id TEXT,
                checkpoint_id TEXT,
                PRIMARY KEY (thread_id, checkpoint_id)
            );
            CREATE TABLE writes (
                thread_id TEXT,
                checkpoint_id TEXT,
                channel TEXT,
                value BLOB
            );
        """)
        if with_memories:
            memories = [{"name": "test", "type": "feedback", "domain": "global",
                         "priority": 20, "tags": "foo", "body": "body text"}]
            packed = msgpack.packb(memories, use_bin_type=True)
            conn.execute(
                "INSERT INTO checkpoints VALUES ('t1', 'c1')"
            )
            conn.execute(
                "INSERT INTO writes VALUES ('t1', 'c1', 'memories', ?)", (packed,)
            )
            conn.execute(
                "INSERT INTO writes VALUES ('t1', 'c1', 'domains', ?)",
                (msgpack.packb(["global"], use_bin_type=True),)
            )


class TestHandleCheckpointQuery:
    def test_missing_db_returns_error(self, tmp_path):
        with patch("src.tools.hooks._DB_PATH", tmp_path / "missing.db"):
            result = handle_checkpoint_query()
        assert "error" in result

    def test_no_checkpoints_returns_error(self, tmp_path):
        db = tmp_path / "cp.db"
        _make_checkpoint_db(db, with_memories=False)
        with patch("src.tools.hooks._DB_PATH", db):
            result = handle_checkpoint_query()
        assert "error" in result

    def test_returns_memories(self, tmp_path):
        db = tmp_path / "cp.db"
        _make_checkpoint_db(db)
        with patch("src.tools.hooks._DB_PATH", db):
            result = handle_checkpoint_query()
        assert result["thread_id"] == "t1"
        assert len(result["memories"]) == 1
        assert result["memories"][0]["name"] == "test"

    def test_filter_by_thread_id(self, tmp_path):
        db = tmp_path / "cp.db"
        _make_checkpoint_db(db)
        with patch("src.tools.hooks._DB_PATH", db):
            result = handle_checkpoint_query(thread_id="t1")
        assert result["thread_id"] == "t1"

    def test_filter_by_wrong_thread_id_returns_error(self, tmp_path):
        db = tmp_path / "cp.db"
        _make_checkpoint_db(db)
        with patch("src.tools.hooks._DB_PATH", db):
            result = handle_checkpoint_query(thread_id="nonexistent")
        assert "error" in result


# ---------------------------------------------------------------------------
# handle_session_id
# ---------------------------------------------------------------------------

class TestHandleSessionId:
    def test_returns_session_id_on_first_try_no_retry(self):
        with patch("urllib.request.urlopen", return_value=_mock_response({"session_id": "sess1", "turn": 5})) as urlopen, \
             patch("time.sleep") as sleep:
            result = handle_session_id()
        assert result == {"session_id": "sess1", "turn": 5}
        assert urlopen.call_count == 1
        sleep.assert_not_called()

    def test_retries_once_when_first_response_empty(self):
        responses = [_mock_response({}), _mock_response({"session_id": "sess2", "turn": 1})]
        with patch("urllib.request.urlopen", side_effect=responses) as urlopen, \
             patch("time.sleep") as sleep:
            result = handle_session_id()
        assert result == {"session_id": "sess2", "turn": 1}
        assert urlopen.call_count == 2
        sleep.assert_called_once()

    def test_returns_error_when_still_empty_after_retry(self):
        with patch("urllib.request.urlopen", return_value=_mock_response({})), \
             patch("time.sleep"):
            result = handle_session_id()
        assert "error" in result

    def test_returns_error_when_server_unreachable(self):
        with patch("urllib.request.urlopen", side_effect=OSError("connection refused")), \
             patch("time.sleep"):
            result = handle_session_id()
        assert "error" in result
