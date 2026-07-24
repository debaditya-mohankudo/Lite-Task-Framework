"""Tests for src/tools/scratch.py — session-scoped in-memory transient store (task:a1181fe6)."""
import pytest

import src.tools.scratch as scratch_module
from src.tools.scratch import (
    handle_clear,
    handle_delete,
    handle_get,
    handle_list,
    handle_set,
)


@pytest.fixture(autouse=True)
def _clean_scratch():
    """Each test gets a fresh module dict — scratch state must not leak
    across tests any more than it should leak across sessions."""
    scratch_module._SCRATCH.clear()
    yield
    scratch_module._SCRATCH.clear()


class TestSetGet:
    def test_set_then_get_round_trip(self):
        assert handle_set("s1", "foo", {"a": 1}) == {"ok": True, "session_id": "s1", "key": "foo"}
        assert handle_get("s1", "foo") == {"found": True, "value": {"a": 1}}

    def test_get_missing_key_not_found(self):
        assert handle_get("s1", "nope") == {"found": False}

    def test_get_missing_session_not_found(self):
        assert handle_get("no-such-session", "foo") == {"found": False}

    def test_set_overwrites_existing_key(self):
        handle_set("s1", "foo", "v1")
        handle_set("s1", "foo", "v2")
        assert handle_get("s1", "foo") == {"found": True, "value": "v2"}

    def test_value_too_large_rejected(self):
        big = "x" * 10000
        result = handle_set("s1", "foo", big)
        assert "error" in result
        assert handle_get("s1", "foo") == {"found": False}

    def test_non_json_serializable_value_rejected(self):
        result = handle_set("s1", "foo", object())
        assert "error" in result


class TestList:
    def test_list_empty_session(self):
        assert handle_list("s1") == {"session_id": "s1", "items": {}}

    def test_list_returns_all_keys(self):
        handle_set("s1", "a", 1)
        handle_set("s1", "b", 2)
        assert handle_list("s1") == {"session_id": "s1", "items": {"a": 1, "b": 2}}


class TestIsolation:
    def test_different_sessions_do_not_share_keys(self):
        handle_set("s1", "foo", "session-1-value")
        handle_set("s2", "foo", "session-2-value")
        assert handle_get("s1", "foo") == {"found": True, "value": "session-1-value"}
        assert handle_get("s2", "foo") == {"found": True, "value": "session-2-value"}


class TestDelete:
    def test_delete_existing_key(self):
        handle_set("s1", "foo", "v")
        assert handle_delete("s1", "foo") == {"ok": True, "deleted": True}
        assert handle_get("s1", "foo") == {"found": False}

    def test_delete_missing_key_is_noop_not_error(self):
        assert handle_delete("s1", "nope") == {"ok": True, "deleted": False}

    def test_delete_missing_session_is_noop_not_error(self):
        assert handle_delete("no-such-session", "foo") == {"ok": True, "deleted": False}


class TestClear:
    def test_clear_removes_all_keys(self):
        handle_set("s1", "a", 1)
        handle_set("s1", "b", 2)
        assert handle_clear("s1") == {"ok": True, "cleared": True}
        assert handle_list("s1") == {"session_id": "s1", "items": {}}

    def test_clear_missing_session_is_noop_not_error(self):
        assert handle_clear("no-such-session") == {"ok": True, "cleared": False}


class TestEviction:
    def test_oldest_session_evicted_once_over_cap(self, monkeypatch):
        monkeypatch.setattr(scratch_module, "_MAX_SESSIONS", 3)
        handle_set("s1", "k", "v")
        handle_set("s2", "k", "v")
        handle_set("s3", "k", "v")
        handle_set("s4", "k", "v")  # pushes total to 4, over the cap of 3
        assert handle_get("s1", "k") == {"found": False}  # oldest, evicted
        assert handle_get("s4", "k")["found"] is True

    def test_writing_to_existing_session_does_not_trigger_eviction(self, monkeypatch):
        monkeypatch.setattr(scratch_module, "_MAX_SESSIONS", 2)
        handle_set("s1", "k", "v1")
        handle_set("s2", "k", "v1")
        handle_set("s1", "k2", "v2")  # same session, should not count as a new one
        assert handle_get("s1", "k") == {"found": True, "value": "v1"}
        assert handle_get("s2", "k") == {"found": True, "value": "v1"}
