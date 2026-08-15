"""drift_hook.py tests — the stdin call-count plumbing, not the gating itself
(that's drift_reflection_nudge's own property, covered in test_dispatcher.py).

The property worth protecting: build_nudge passes call_count through to
dispatcher.drift_reflection_nudge unchanged, and main() tolerates a payload
with no "_taskfw_drift_call_count" field (pre-task:1c8f0815 callers, or a
malformed/empty stdin) by treating it as no counter at all.
"""
from __future__ import annotations

import io
import json

from taskfw.drift_hook import build_nudge, main
from taskfw.models import Task
from taskfw.store import TaskStore


def _store_with_active(tmp_path, active_id="t1"):
    store = TaskStore(tmp_path / "t.db")
    store.save(Task(id=active_id, type="task", title="Some task"))
    store.push_active(active_id, scope="scope")
    return store


class TestBuildNudge:
    def test_none_without_an_active_task(self, tmp_path):
        store = TaskStore(tmp_path / "t.db")
        assert build_nudge(store, "scope", None) is None

    def test_passes_call_count_through_to_gating(self, tmp_path):
        store = _store_with_active(tmp_path)
        assert build_nudge(store, "scope", 1) is None
        assert build_nudge(store, "scope", 3) is not None

    def test_none_call_count_fires(self, tmp_path):
        store = _store_with_active(tmp_path)
        assert build_nudge(store, "scope", None) is not None


class TestMainStdinParsing:
    def test_missing_counter_field_treated_as_none(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TASKFW_SCOPE", "scope")
        monkeypatch.setattr("taskfw.drift_hook.TaskStore", lambda: _store_with_active(tmp_path))
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"tool_name": "Bash"})))
        assert main() == 0

    def test_non_int_counter_field_treated_as_none(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TASKFW_SCOPE", "scope")
        monkeypatch.setattr("taskfw.drift_hook.TaskStore", lambda: _store_with_active(tmp_path))
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"_taskfw_drift_call_count": "3"})))
        assert main() == 0

    def test_malformed_stdin_fails_open(self, monkeypatch):
        monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
        assert main() == 0
