"""task:87c91a76 — tasks__open_in_kitty shells out to `kitty-taskfw open`, fail-soft.

No test forks: the runner is injected, and KITTY_* is cleared so a suite run
inside kitty cannot touch live tabs.
"""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

from taskfw import kitty_open
from taskfw import mcp_server as m
from taskfw.store import TaskStore


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    for k in [k for k in __import__("os").environ if k.startswith("KITTY_")]:
        monkeypatch.delenv(k)
    monkeypatch.delenv(kitty_open.CMD_ENV, raising=False)
    s = TaskStore(tmp_path / "t.db")
    m.set_store(s)
    monkeypatch.setenv("TASKFW_SCOPE", "/test/workspace")
    yield s
    s.close()
    m.set_store(None)


def fake(stdout="", stderr="", code=0, raises=None):
    calls = []

    def runner(argv, **kw):
        calls.append((argv, kw))
        if raises:
            raise raises
        return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=code)

    runner.calls = calls
    return runner


ENV = {"TASKFW_KITTY_OPEN_CMD": "kt-fake --flag"}


class TestOpenTask:
    def test_opened_result_is_returned_and_argv_is_explicit(self):
        r = fake(json.dumps({"status": "opened", "window_id": 7, "socket": "/s", "reason": None}))
        out = kitty_open.open_task("abc", "/repo", runner=r, env=ENV)
        assert out == {"status": "opened", "window_id": 7, "socket": "/s", "reason": None}
        argv, kw = r.calls[0]
        assert argv == ["kt-fake", "--flag", "open", "--worktree", "/repo", "abc"]
        assert kw["timeout"] == kitty_open.TIMEOUT_S

    def test_exists_passes_through(self):
        r = fake(json.dumps({"status": "exists", "window_id": 3}))
        assert kitty_open.open_task("abc", "/r", runner=r, env=ENV)["status"] == "exists"

    def test_missing_command_is_not_opened(self):
        r = fake()
        out = kitty_open.open_task("abc", "/r", runner=r, env={"PATH": ""})
        assert out["status"] == "not_opened" and "not found" in out["reason"]
        assert r.calls == []

    def test_path_lookup_is_used_without_override(self, tmp_path):
        exe = tmp_path / "kitty-taskfw"
        exe.write_text("#!/bin/sh\n")
        exe.chmod(0o755)
        r = fake(json.dumps({"status": "opened"}))
        kitty_open.open_task("abc", "/r", runner=r, env={"PATH": str(tmp_path)})
        assert r.calls[0][0][0] == str(exe)

    @pytest.mark.parametrize("stdout", ["", "Traceback...", "[]", '{"status": "weird"}'])
    def test_bad_output_is_not_opened(self, stdout):
        out = kitty_open.open_task("abc", "/r", runner=fake(stdout, "boom", 1), env=ENV)
        assert out["status"] == "not_opened" and out["reason"]

    def test_timeout_is_not_opened(self):
        r = fake(raises=subprocess.TimeoutExpired("x", 1))
        out = kitty_open.open_task("abc", "/r", runner=r, env=ENV)
        assert out["status"] == "not_opened" and "timed out" in out["reason"]

    def test_oserror_is_not_opened(self):
        out = kitty_open.open_task("abc", "/r", runner=fake(raises=PermissionError("no")), env=ENV)
        assert out["status"] == "not_opened" and "PermissionError" in out["reason"]


class TestTool:
    def test_unknown_task_is_an_error(self):
        assert "error" in m.tasks__open_in_kitty("nope", worktree="/r")

    def test_no_known_directory_does_not_guess(self, monkeypatch):
        t = m.tasks__create(title="x")
        monkeypatch.setattr(m.kitty_open, "open_task", lambda *a, **k: pytest.fail("ran"))
        monkeypatch.setattr(m.scope_mod, "local_root", lambda scope: None)
        out = m.tasks__open_in_kitty(t["id"])
        assert out["status"] == "not_opened" and "pass worktree" in out["reason"]
        assert "error" not in out

    def test_explicit_worktree_reaches_the_cli_and_pointer_is_untouched(self, monkeypatch):
        t = m.tasks__create(title="x")
        seen = {}
        monkeypatch.setattr(
            m.kitty_open, "open_task",
            lambda tid, wt: seen.update(tid=tid, wt=wt) or {"status": "opened"},
        )
        before = m.store().get_active(m._workspace())
        assert m.tasks__open_in_kitty(t["id"], worktree="/w")["status"] == "opened"
        assert seen == {"tid": t["id"], "wt": "/w"}
        assert m.store().get_active(m._workspace()) == before

    def test_task_id_is_required(self):
        with pytest.raises(TypeError):
            m.tasks__open_in_kitty()
