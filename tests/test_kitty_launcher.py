import json
import subprocess

import pytest

from taskfw.kitty.adapter import KittyAdapter
from taskfw.kitty.launcher import Launcher, NoSessionToResume, claude_cmd, mcp_choice, plain_tab
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
    assert argv[argv.index("--") + 1:] == ["/bin/claude", "--model", "claude-sonnet-5", "--", "/some-skill"]
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


def test_plain_with_parent_ends_the_prompt_with_the_shared_report_back_sentence(tmp_path):
    f = Fake(handler)
    ad = KittyAdapter("/tmp/s", kitten="/k/kitten", runner=f, check_version=False)
    plain_tab(ad, str(tmp_path), "do X", claude="/bin/claude", path="/bin", parent="sess-9")
    prompt = next(c for c in f.calls if "launch" in c)[-1]
    assert prompt.startswith("do X When finished call SendMessage to sess-9 with the outcome in one line")
    assert prompt.endswith("If SendMessage fails, say so in your final output and stop.")


def test_plain_refuses_a_multiline_parent_before_touching_kitty(tmp_path):
    from taskfw.kitty.adapter import KittyError
    f = Fake(handler)
    ad = KittyAdapter("/tmp/s", kitten="/k/kitten", runner=f, check_version=False)
    with pytest.raises(KittyError):
        plain_tab(ad, str(tmp_path), "x", parent="a\nb")
    assert f.calls == []


def _claude_json(tmp_path, servers):
    p = tmp_path / "claude.json"
    p.write_text(json.dumps({"mcpServers": servers}))
    return p


SERVERS = {"taskfw": {"command": "/a"}, "claude-hooks": {"command": "/b"}, "local-mac": {"command": "/c"}}


def test_slim_config_keeps_only_task_servers(tmp_path):
    cfg = json.loads(mcp_choice({}, _claude_json(tmp_path, SERVERS))[0])
    assert cfg == {"mcpServers": {"taskfw": {"command": "/a"}, "claude-hooks": {"command": "/b"}}}


def test_slim_config_names_overridable_and_all_disables(tmp_path):
    p = _claude_json(tmp_path, SERVERS)
    assert set(json.loads(mcp_choice({"TASKFW_KITTY_MCP": "taskfw"}, p)[0])["mcpServers"]) == {"taskfw"}
    assert mcp_choice({"TASKFW_KITTY_MCP": "all"}, p)[0] is None
    assert mcp_choice({"TASKFW_KITTY_MCP": ""}, p)[0] is None


def test_slim_config_fails_open(tmp_path):
    assert mcp_choice({}, tmp_path / "missing.json")[0] is None
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert mcp_choice({}, bad)[0] is None
    assert mcp_choice({}, _claude_json(tmp_path, {"taskfw": {}}))[0] is None  # claude-hooks absent: no partial set


def test_claude_cmd_pins_strict_flags_before_resume_and_prompt():
    cfg = '{"mcpServers": {}}'
    assert claude_cmd("c", "/h", "sess", "go", cfg) == [
        "c", "--settings", "/h", "--strict-mcp-config", "--mcp-config", cfg, "--resume", "sess", "--", "go"]
    assert "--strict-mcp-config" not in claude_cmd("c", "/h", None, None)


def test_launcher_passes_mcp_config_to_start_and_resume_but_plain_tab_never(tmp_path):
    lau, st, f = make(handler, tmp_path)
    lau.mcp_config = '{"mcpServers": {"taskfw": {"env": {"TOKEN": "s3cret"}}}}'
    lau.mcp_dir = tmp_path / "mcp"
    lau.start("abc", str(tmp_path))
    assert "--strict-mcp-config" in argv_tail(f)
    assert "s3cret" not in " ".join(next(c for c in reversed(f.calls) if "launch" in c))  # secrets live in the file, never in argv
    (cfg,) = lau.mcp_dir.glob("mcp-*.json")
    assert cfg.read_text() == lau.mcp_config and cfg.stat().st_mode & 0o777 == 0o600
    assert str(cfg) in argv_tail(f)
    lau.resume("abc", fresh=True)
    assert "--strict-mcp-config" in argv_tail(f)
    plain_tab(lau.adapter, str(tmp_path), claude="/bin/claude")
    assert "--strict-mcp-config" not in argv_tail(f)


def test_write_mcp_config_replaces_a_partial_file_and_an_interrupted_write_leaves_none(tmp_path, monkeypatch):
    from taskfw.kitty import launcher
    config = '{"mcpServers": {"taskfw": {"command": "x"}}}'
    path = launcher.write_mcp_config(config, tmp_path)
    path.write_text(config[:10])  # what an interrupted write used to leave behind, then reuse forever
    assert launcher.write_mcp_config(config, tmp_path) == path and path.read_text() == config
    path.unlink()

    def boom(*a):
        raise OSError("disk full")
    monkeypatch.setattr(launcher.os, "replace", boom)  # interrupted after the temp write, before the rename
    with pytest.raises(launcher.KittyError, match="disk full"):
        launcher.write_mcp_config(config, tmp_path)
    assert list(tmp_path.iterdir()) == []  # neither the named file nor a stray temp


def test_mcp_choice_names_why_each_config_was_or_was_not_built(tmp_path):
    p = _claude_json(tmp_path, SERVERS)
    assert mcp_choice({}, p)[1] == "default"
    assert mcp_choice({"TASKFW_KITTY_MCP": "taskfw"}, p)[1] == "override"
    assert mcp_choice({"TASKFW_KITTY_MCP": "all"}, p) == (None, "all-requested")
    assert mcp_choice({"TASKFW_KITTY_MCP": ""}, p) == (None, "all-requested")
    assert mcp_choice({}, tmp_path / "missing.json") == (None, "fallback:claude-json-unreadable")
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert mcp_choice({}, bad) == (None, "fallback:claude-json-malformed")
    no_key = tmp_path / "nokey.json"
    no_key.write_text("{}")
    assert mcp_choice({}, no_key) == (None, "fallback:no-mcpServers")
    assert mcp_choice({}, _claude_json(tmp_path, {"taskfw": {}})) == (None, "fallback:server-absent=claude-hooks")
    assert mcp_choice({"TASKFW_KITTY_MCP": "nope"}, p) == (None, "fallback:server-absent=nope")
    # a requested server literally named mcpServers is absent or present, never "no-mcpServers"
    assert mcp_choice({"TASKFW_KITTY_MCP": "mcpServers"}, p) == (None, "fallback:server-absent=mcpServers")
    named = _claude_json(tmp_path, {"mcpServers": {"cmd": "x"}})
    assert mcp_choice({"TASKFW_KITTY_MCP": "mcpServers"}, named)[1] == "override"


def _launch_line(tmp_path, task, **kw):
    lau, _, _ = make(handler, tmp_path)
    lau.mcp_dir = tmp_path / "mcp"
    lau.claude_json = lau.settings_json = tmp_path / "absent.json"  # never probe the real home
    for k, v in kw.items():
        setattr(lau, k, v)
    lau.start(task, str(tmp_path))
    (line,) = kitty_log_lines(f"task={task} ")
    return line


def test_launch_line_states_the_mcp_set_and_why_for_slim_override_and_fallback(tmp_path):
    p = _claude_json(tmp_path, SERVERS)
    for task, env, names, source in [
        ("mcpslim1", {}, "taskfw,claude-hooks", "default"),
        ("mcpover1", {"TASKFW_KITTY_MCP": "taskfw"}, "taskfw", "override"),
        ("mcpfall1", {"TASKFW_KITTY_MCP": "nope"}, "all", "fallback:server-absent=nope"),
        ("mcpall01", {"TASKFW_KITTY_MCP": "all"}, "all", "all-requested"),
    ]:
        cfg, why = mcp_choice(env, p)
        line = _launch_line(tmp_path, task, mcp_config=cfg, mcp_source=why)
        assert f"mcp={names} mcp_source={source}" in line


def test_bare_launcher_and_plain_tab_log_without_a_mcp_decision(tmp_path):
    assert "mcp=all mcp_source=none" in _launch_line(tmp_path, "mcpbare1")
    lau, _, _ = make(handler, tmp_path)
    plain_tab(lau.adapter, str(tmp_path), claude="/bin/claude")
    assert not any(" mcp=" in m for m in kitty_log_lines("kitty plain"))


def test_override_naming_no_server_falls_back_to_default_and_says_so(tmp_path):
    p = _claude_json(tmp_path, SERVERS)
    for raw in (",", " , ,"):
        cfg, why = mcp_choice({"TASKFW_KITTY_MCP": raw}, p)
        assert set(json.loads(cfg)["mcpServers"]) == {"taskfw", "claude-hooks"}
        assert why == "fallback:override-empty"


def test_launch_line_names_servers_the_slim_set_drops(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".mcp.json").write_text("{}")
    cj = tmp_path / "claude.json"
    cj.write_text(json.dumps({"mcpServers": SERVERS, "projects": {str(proj): {"mcpServers": {"local": {}}}}}))
    st = tmp_path / "settings.json"
    st.write_text(json.dumps({"enabledPlugins": {"p@m": True, "off@m": False}}))
    cfg, why = mcp_choice({}, cj)
    lau, _, _ = make(handler, tmp_path)
    lau.mcp_dir, lau.claude_json, lau.settings_json = tmp_path / "mcp", cj, st
    lau.mcp_config, lau.mcp_source = cfg, why
    lau.start("dropt1", str(proj))
    line = kitty_log_lines("task=dropt1 ")[0]
    assert f"mcp_source=default;drops={proj / '.mcp.json'},project:local,plugin:p@m" in line
    assert "off@m" not in line
    lau.claude_json = lau.settings_json = tmp_path / "none.json"
    (proj / ".mcp.json").unlink()
    lau.start("dropt2", str(proj))
    assert "mcp_source=default" in kitty_log_lines("task=dropt2 ")[0] and "drops" not in kitty_log_lines("task=dropt2 ")[0]


def test_torn_claude_json_read_is_retried_not_reported_as_full_set(tmp_path, monkeypatch):
    p = tmp_path / "claude.json"
    p.write_text('{"mcpServers": {"taskfw"')  # mid-write
    monkeypatch.setattr("taskfw.kitty.launcher.time.sleep",
                        lambda _: p.write_text(json.dumps({"mcpServers": SERVERS})))
    cfg, why = mcp_choice({}, p)
    assert cfg is not None and why == "default"
    p.write_text("{not json")  # never heals: still reported as malformed
    monkeypatch.setattr("taskfw.kitty.launcher.time.sleep", lambda _: None)
    assert mcp_choice({}, p) == (None, "fallback:claude-json-malformed")
