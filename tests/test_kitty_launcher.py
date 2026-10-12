import json
import subprocess

import pytest

from taskfw.kitty.adapter import KittyAdapter
from taskfw.kitty.launcher import Launcher, NoSessionToResume, plain_tab
from taskfw.kitty.store import Store


def cp(rc=0, out="", err=""):
    return subprocess.CompletedProcess([], rc, out, err)


def ls_json(*wins):
    return json.dumps([{"id": 1, "tabs": [{"id": t, "windows": [
        {"id": w, "user_vars": uv} for (w, uv) in [(wid, v)]]} for (t, wid, v) in wins]}])


class Fake:
    def __init__(self, handler):
        self.handler, self.calls = handler, []

    def __call__(self, argv):
        self.calls.append(list(argv))
        return self.handler(list(argv))


def make(handler, tmp_path):
    f = Fake(handler)
    ad = KittyAdapter("/tmp/s", kitten="/k/kitten", runner=f, check_version=False)
    st = Store(":memory:")
    return Launcher(ad, st, hooks="/h/settings.json", claude="/bin/claude", db="/db/t.db",
                    path="/opt/bin:/usr/bin"), st, f


_next = iter(range(7, 1000))
_last = [7]


def handler(argv):
    # real kitty hands out a fresh window id per launch
    if "launch" in argv:
        _last[0] = next(_next)
        return cp(out=f"{_last[0]}\n")
    if "ls" in argv and "--match" not in argv:
        return cp(out=ls_json((3, _last[0], {"task_id": "abc"})))
    if "ls" in argv:
        wid = int(argv[argv.index("--match") + 1].split(":")[1])
        return cp(out=ls_json((3, wid, {"task_id": "abc"})))
    return cp()


def test_start_passes_identity_path_vars_and_records(tmp_path):
    lau, st, f = make(handler, tmp_path)
    wt = tmp_path / "work tree"  # space on purpose
    wt.mkdir()
    wid = lau.start("abc", str(wt))
    assert wid >= 7
    argv = next(c for c in f.calls if "launch" in c)
    assert "--env" in argv and f"TASKFW_TASK_ID=abc" in argv
    assert f"TASKFW_WORKTREE={wt.resolve()}" in argv
    assert "TASKFW_DB=/db/t.db" in argv and "PATH=/opt/bin:/usr/bin" in argv
    assert "task_id=abc" in argv and f"--cwd={wt.resolve()}" in argv
    assert argv[argv.index("--") + 1:] == ["/bin/claude", "--settings", "/h/settings.json"]
    row = st.latest_for_task("abc")
    assert (row.window_id, row.tab_id, row.claude_session_id) == (wid, 3, None)


def test_resume_uses_backfilled_session_in_new_tab(tmp_path):
    lau, st, f = make(handler, tmp_path)
    wid = lau.start("abc", str(tmp_path))
    assert st.backfill_session(socket="unix:/tmp/s", window_id=wid, session_id="sess-1")
    lau.resume("abc")
    argv = [c for c in f.calls if "launch" in c][-1]
    assert argv[-2:] == ["--resume", "sess-1"]
    assert len(st.list()) == 2  # always a new tab, never re-pointed


def test_resume_null_session_reports_or_goes_fresh(tmp_path):
    lau, st, f = make(handler, tmp_path)
    lau.start("abc", str(tmp_path))
    with pytest.raises(NoSessionToResume):
        lau.resume("abc")
    lau.resume("abc", fresh=True)
    assert "--resume" not in [c for c in f.calls if "launch" in c][-1]


def test_prompt_is_one_trailing_argv_after_double_dash(tmp_path):
    lau, st, f = make(handler, tmp_path)
    p = "-x it's \"q\"\nline2"
    lau.start("abc", str(tmp_path), prompt=p)
    argv = next(c for c in f.calls if "launch" in c)
    assert argv[-2:] == ["--", p] and argv.count("--") == 2


def test_resume_fresh_has_no_prompt_unless_given(tmp_path):
    lau, st, f = make(handler, tmp_path)
    lau.start("abc", str(tmp_path))
    lau.resume("abc", fresh=True)
    assert argv_tail(f) == ["/bin/claude", "--settings", "/h/settings.json"]
    lau.resume("abc", fresh=True, prompt="go")
    assert argv_tail(f)[-2:] == ["--", "go"]


def argv_tail(f):
    a = [c for c in f.calls if "launch" in c][-1]
    return a[a.index("--") + 1:]


def test_list_marks_gone_windows(tmp_path):
    lau, st, f = make(handler, tmp_path)
    lau.start("abc", str(tmp_path))
    st.record_launch(task_id="old", window_id=99, tab_id=None, socket="unix:/tmp/s", cwd="/x")
    states = {t: s for t, _, s in lau.list()}
    assert states == {"abc": "-", "old": "gone"}


def test_store_path_passed_to_hooks_but_not_for_memory_store(tmp_path):
    lau, _, f = make(handler, tmp_path)
    lau.start("abc", str(tmp_path))
    argv = next(c for c in f.calls if "launch" in c)
    assert not any(a.startswith("KITTY_TASKFW_STORE=") for a in argv)  # :memory: is not shareable
    ad = KittyAdapter("/tmp/s", kitten="/k/kitten", runner=Fake(handler), check_version=False)
    f2 = ad._runner
    lau2 = Launcher(ad, Store(str(tmp_path / "s.sqlite")), hooks="/h/s.json", claude="/bin/claude",
                    db="/db/t.db", path="/usr/bin")
    lau2.start("abc", str(tmp_path))
    argv2 = next(c for c in f2.calls if "launch" in c)
    assert f"KITTY_TASKFW_STORE={tmp_path / 's.sqlite'}" in argv2


def test_start_attaches_the_watcher_per_window(tmp_path):
    lau, st, f = make(handler, tmp_path)
    lau.start("abc", str(tmp_path))
    argv = next(c for c in f.calls if "launch" in c)
    assert any(a.startswith("--watcher=") and a.endswith("watcher.py") for a in argv)


def test_console_script_targets_launcher_main():
    import tomllib
    from pathlib import Path
    proj = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
    assert proj["project"]["scripts"]["taskfw-kitty"] == "taskfw.kitty.launcher:main"


def test_missing_hooks_file_is_reported_not_launched(tmp_path):
    from taskfw.kitty.launcher import check_install
    assert check_install(tmp_path / "nope.json") and "not found" in check_install(tmp_path / "nope.json")
    (tmp_path / "ok.json").write_text("{}")
    assert check_install(tmp_path / "ok.json") is None


def test_open_and_start_refuse_a_broken_install_before_touching_kitty(monkeypatch, capsys, tmp_path):
    import json
    from taskfw.kitty import launcher, opener
    monkeypatch.setattr(launcher, "DEFAULT_HOOKS", tmp_path / "nope.json")
    r = opener.open_task("abc", str(tmp_path), socket="/tmp/kitty-1", store=str(tmp_path / "s.sqlite"),
                         env={"PATH": "/bin"}, claude="/bin/claude",
                         adapter_factory=lambda s: (_ for _ in ()).throw(AssertionError("touched kitty")))
    assert r.status == "not_opened" and "hooks file" in r.reason
    rc = launcher.main(["--socket", "unix:/tmp/kitty-1", "--store", str(tmp_path / "s.sqlite"),
                        "start", "abc"], env={})
    assert rc == 1 and "hooks file" in capsys.readouterr().err


def test_plain_launches_a_tab_with_no_task_identity_hooks_or_watcher(tmp_path):
    f = Fake(handler)
    ad = KittyAdapter("/tmp/s", kitten="/k/kitten", runner=f, check_version=False)
    wid = plain_tab(ad, str(tmp_path), "/some-skill", "skill", claude="/bin/claude", path="/opt/bin:/usr/bin")
    argv = next(c for c in f.calls if "launch" in c)
    assert wid >= 7 and f"--cwd={tmp_path.resolve()}" in argv and "--tab-title=skill" in argv
    assert argv[argv.index("--") + 1:] == ["/bin/claude", "--", "/some-skill"]
    assert "PATH=/opt/bin:/usr/bin" in argv
    assert not any(a.startswith(("TASKFW_", "KITTY_TASKFW", "--watcher")) for a in argv)


def test_plain_refuses_a_missing_directory_before_touching_kitty(tmp_path):
    from taskfw.kitty.adapter import KittyError
    f = Fake(handler)
    ad = KittyAdapter("/tmp/s", kitten="/k/kitten", runner=f, check_version=False)
    with pytest.raises(KittyError):
        plain_tab(ad, str(tmp_path / "nope"))
    assert f.calls == []


def kitty_log_lines(*needles):
    """Messages the test-session log sink holds for the kitty logger that mention every needle."""
    import os
    with open(os.environ["TASKFW_LOG_JSONL"]) as f:
        rows = [json.loads(line) for line in f if line.strip()]
    return [r["message"] for r in rows if r["logger"] == "taskfw.kitty" and all(n in r["message"] for n in needles)]


def test_start_and_resume_each_log_one_launch_line_naming_the_resumed_session(tmp_path):
    lau, st, _ = make(handler, tmp_path)
    lau.start("logtask1", str(tmp_path))
    st.backfill_session(socket="unix:/tmp/s", window_id=_last[0], session_id="sess-1")
    lau.resume("logtask1", str(tmp_path))
    first, second = kitty_log_lines("task=logtask1")
    assert "resumed_session=-" in first and "resumed_session=sess-1" in second
    assert f"cwd={tmp_path.resolve()}" in second


def test_plain_logs_a_line_with_no_task(tmp_path):
    f = Fake(handler)
    ad = KittyAdapter("/tmp/s", kitten="/k/kitten", runner=f, check_version=False)
    wid = plain_tab(ad, str(tmp_path), claude="/bin/claude", path="/bin")
    assert kitty_log_lines(f"kitty plain window={wid} ", f"cwd={tmp_path.resolve()}")
