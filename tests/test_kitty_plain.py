"""task:4582de54 — a throwaway kitty tab that belongs to no task and lives at most an hour.

tasks__open_plain_tab shells out to `taskfw.kitty.launcher open-plain` (fail-soft, like
tasks__open_in_kitty); plain_tab wraps claude in `perl -e 'alarm ...; exec'` for the time limit.
No test forks kitty: the runner and the adapter are injected, and KITTY_* is cleared.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

from taskfw import kitty_open
from taskfw import mcp_server as m
from taskfw.kitty import opener
from taskfw.kitty.adapter import KittyError
from taskfw.kitty.launcher import plain_tab
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


def runner_returning(payload):
    calls = []

    def runner(argv, **kw):
        calls.append(argv)
        return SimpleNamespace(stdout=json.dumps(payload), stderr="", returncode=0)

    runner.calls = calls
    return runner


OPENED = {"status": "opened", "window_id": 7, "socket": "unix:/tmp/k", "reason": None,
          "model": "claude-sonnet-5", "model_source": "caller: cheap check", "extra": "dropped"}


def test_open_plain_tab_passes_every_argument_and_returns_only_the_contract_keys():
    r = runner_returning(OPENED)
    out = kitty_open.open_plain_tab("/w", "sess-1", "do X", "scratch", "claude-sonnet-5", "cheap check", 30,
                                    runner=r, env={})
    argv = r.calls[0]
    assert argv[argv.index("open-plain"):argv.index("--max-life-minutes")] == [
        "open-plain", "--cwd", "/w", "--parent", "sess-1", "--prompt", "do X", "--title", "scratch",
        "--model", "claude-sonnet-5", "--model-reason", "cheap check"]
    assert "--max-life-minutes" in argv and argv[argv.index("--max-life-minutes") + 1] == "30"
    assert set(out) == {"status", "window_id", "socket", "reason", "model", "model_source"}


@pytest.mark.parametrize("bad", [
    dict(parent=""), dict(parent="a\nb"), dict(prompt="  "),
    dict(max_life_minutes=0), dict(max_life_minutes=61),
])
def test_tool_refuses_bad_arguments_before_forking(bad, monkeypatch):
    monkeypatch.setattr(kitty_open, "open_plain_tab",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("forked")))
    args = {"parent": "sess-1", "prompt": "do X", **bad}
    out = m.tasks__open_plain_tab(**args)
    assert out["status"] == "not_opened" and out["reason"]


def test_tool_opened_result_reports_the_life_and_the_fallback_model(monkeypatch):
    monkeypatch.setattr(kitty_open, "open_plain_tab", lambda *a, **k: dict(OPENED, extra=None))
    out = m.tasks__open_plain_tab("sess-1", "do X")
    assert out["closes_in_minutes"] == 60 and "model_note" in out
    out = m.tasks__open_plain_tab("sess-1", "do X", model="claude-sonnet-5", model_reason="cheap", max_life_minutes=5)
    assert out["closes_in_minutes"] == 5 and "model_note" not in out


def test_tool_leaves_no_task_row_and_the_active_pointer_alone(store, monkeypatch):
    monkeypatch.setattr(kitty_open, "open_plain_tab", lambda *a, **k: dict(OPENED))
    before_rows = store.list()
    m.tasks__open_plain_tab("sess-1", "do X")
    after_rows = store.list()
    assert before_rows == after_rows
    assert m.tasks__active().get("active") in (None, "")


class FakeAdapter:
    def __init__(self):
        self.launched = []

    def launch(self, **kw):
        self.launched.append(kw)
        return 11


def test_plain_tab_without_a_limit_keeps_the_plain_argv(tmp_path):
    ad = FakeAdapter()
    plain_tab(ad, str(tmp_path), "x", claude="/bin/claude", parent="sess-1")
    assert ad.launched[0]["cmd"][0] == "/bin/claude"


@pytest.mark.skipif(not shutil.which("perl"), reason="perl is how the limit is enforced")
def test_plain_tab_with_a_limit_runs_claude_under_a_perl_alarm(tmp_path):
    ad = FakeAdapter()
    plain_tab(ad, str(tmp_path), "x", claude="/bin/claude", parent="sess-1", max_life_minutes=2)
    cmd = ad.launched[0]["cmd"]
    assert cmd[1:4] == ["-e", "alarm shift; exec @ARGV", "120"] and cmd[4] == "/bin/claude"


@pytest.mark.parametrize("minutes", [0, -3, "5"])
def test_plain_tab_refuses_a_bad_limit_before_launching(tmp_path, minutes):
    ad = FakeAdapter()
    with pytest.raises(KittyError, match="max_life_minutes"):
        plain_tab(ad, str(tmp_path), "x", claude="/bin/claude", parent="sess-1", max_life_minutes=minutes)
    assert ad.launched == []


def test_plain_tab_without_perl_refuses_rather_than_running_unlimited(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: None if name == "perl" else "/bin/" + name)
    ad = FakeAdapter()
    with pytest.raises(KittyError, match="perl"):
        plain_tab(ad, str(tmp_path), "x", claude="/bin/claude", parent="sess-1", max_life_minutes=5)
    assert ad.launched == []


@pytest.mark.skipif(not shutil.which("perl"), reason="needs perl")
def test_the_alarm_wrapper_really_ends_a_foreground_child():
    p = subprocess.run([shutil.which("perl"), "-e", "alarm shift; exec @ARGV", "1", sys.executable,
                        "-c", "import time; time.sleep(30)"], timeout=20)
    assert p.returncode == -14  # SIGALRM


def test_open_plain_is_not_opened_with_no_kitty_socket(tmp_path):
    r = opener.open_plain(str(tmp_path), parent="sess-1", env={}, socket_glob=str(tmp_path / "none-*"))
    assert r.status == "not_opened" and "no kitty socket" in r.reason


def test_open_plain_launches_on_the_one_live_socket(tmp_path, monkeypatch):
    class Ad:
        def __init__(self, sock):
            self.sock = sock

        def ls(self):
            return []

    seen = {}
    monkeypatch.setattr(opener, "plain_tab", lambda ad, cwd, prompt, title, **kw: seen.update(kw) or 9)
    r = opener.open_plain(str(tmp_path), socket="/tmp/k-1", env={}, adapter_factory=Ad, parent="sess-1",
                          max_life_minutes=7)
    assert r.status == "opened" and r.window_id == 9 and seen["max_life_minutes"] == 7


def test_open_plain_never_raises(tmp_path, monkeypatch):
    class Ad:
        def __init__(self, sock):
            pass

        def ls(self):
            return []

    monkeypatch.setattr(opener, "plain_tab", lambda *a, **k: (_ for _ in ()).throw(KittyError("parent is required")))
    r = opener.open_plain(str(tmp_path), socket="/tmp/k-1", env={}, adapter_factory=Ad, parent="")
    assert r.status == "not_opened" and "parent is required" in r.reason
