import json
import subprocess

import pytest

from taskfw.kitty import models
from taskfw.kitty.adapter import KittyAdapter
from taskfw.kitty.launcher import Launcher, claude_cmd, plain_tab, record_model_decision
from taskfw.kitty.store import Store
from taskfw import kitty_open


def cp(out=""):
    return subprocess.CompletedProcess([], 0, out, "")


class Fake:
    def __init__(self):
        self.calls, self.wid = [], 4

    def __call__(self, argv):
        self.calls.append(list(argv))
        if "launch" in argv:
            self.wid += 1
            return cp(f"{self.wid}\n")
        if "ls" in argv:
            return cp(json.dumps([{"id": 1, "tabs": [{"id": 3, "windows": [{"id": self.wid, "user_vars": {}}]}]}]))
        return cp()


def make(tmp_path, **kw):
    f = Fake()
    ad = KittyAdapter("/tmp/s", kitten="/k/kitten", runner=f, check_version=False)
    st = Store(":memory:")
    lau = Launcher(ad, st, hooks="/h/s.json", claude="/bin/claude", db=str(tmp_path / "t.db"), **kw)
    return lau, st, f


def launched(f):
    argv = next(c for c in f.calls if "launch" in c)
    return argv[argv.index("/bin/claude"):]


def test_each_tier_is_reachable_for_tasks():
    big = ["a", "b", "c", "d", "e"]
    assert models.for_task(epic=False, title="rename x", motivation="", resolution=["a"], tags=[])[0] == models.SONNET_5
    assert models.for_task(epic=False, title="t", motivation="", resolution=["a", "b", "c"], tags=[])[0] == models.SONNET_5_5
    assert models.for_task(epic=False, title="t", motivation="", resolution=big, tags=[])[0] == models.OPUS_5
    assert models.for_task(epic=False, title="redesign the schema", motivation="", resolution=[], tags=[])[0] == models.OPUS_5_5
    assert models.for_task(epic=True, title="t", motivation="", resolution=[], tags=[])[0] == models.OPUS_5_5


def test_each_tier_is_reachable_for_prompts():
    assert models.for_prompt("/skill")[0] == models.SONNET_5
    assert models.for_prompt("word " * 20)[0] == models.SONNET_5_5
    assert models.for_prompt("word " * 70)[0] == models.OPUS_5
    assert models.for_prompt("investigate a race")[0] == models.OPUS_5_5
    assert models.for_prompt(None)[0] == models.SONNET_5


def test_override_wins_and_decide_is_never_called():
    def boom():
        raise AssertionError("called")
    assert models.pick(" claude-opus-5 ", boom) == ("claude-opus-5", "caller (no reason given)")
    assert models.pick("claude-opus-5", boom, "touches the\n  schema") == ("claude-opus-5", "caller: touches the schema")


@pytest.mark.parametrize("decide", [lambda: 1 / 0, lambda: ("claude-nope", "x")])
def test_a_failing_or_unknown_decision_falls_back_to_the_default(decide):
    model, source = models.pick(None, decide)
    assert model is None and source.startswith("fallback:")


def test_claude_cmd_puts_model_before_the_prompt_and_after_mcp():
    cmd = claude_cmd("c", "/h", "sid", "go", "/m.json", "claude-opus-5")
    assert cmd == ["c", "--settings", "/h", "--strict-mcp-config", "--mcp-config", "/m.json",
                   "--model", "claude-opus-5", "--resume", "sid", "--", "go"]
    assert "--model" not in claude_cmd("c", "/h")


def test_launcher_chooses_records_and_resume_keeps_the_model(tmp_path):
    lau, st, f = make(tmp_path, task_model=lambda tid: (models.OPUS_5, "large"))
    lau.start("abc", str(tmp_path))
    assert launched(f)[1:3] == ["--settings", "/h/s.json"] and "--model" in launched(f)
    assert launched(f)[launched(f).index("--model") + 1] == models.OPUS_5
    row = st.latest_for_task("abc")
    st.backfill_session(socket=row.socket, window_id=5, session_id="sid")
    lau.task_model = lambda tid: (models.SONNET_5, "small")
    f.calls.clear()
    lau.resume("abc", str(tmp_path))
    argv = launched(f)
    assert argv[argv.index("--model") + 1] == models.OPUS_5 and "--resume" in argv


def test_launcher_override_beats_the_choice_and_failure_launches_default(tmp_path):
    lau, _, f = make(tmp_path, model="claude-sonnet-5-5", task_model=lambda tid: (models.OPUS_5, "x"))
    lau.start("abc", str(tmp_path))
    assert launched(f)[launched(f).index("--model") + 1] == "claude-sonnet-5-5"
    lau2, _, f2 = make(tmp_path, task_model=lambda tid: 1 / 0)
    lau2.start("abc", str(tmp_path))
    assert "--model" not in launched(f2)


def test_launcher_without_a_chooser_adds_no_flag(tmp_path):
    lau, _, f = make(tmp_path)
    lau.start("abc", str(tmp_path))
    assert "--model" not in launched(f)


def test_plain_tab_chooses_and_reports_on_stderr(tmp_path, capsys):
    f = Fake()
    ad = KittyAdapter("/tmp/s", kitten="/k/kitten", runner=f, check_version=False)
    plain_tab(ad, str(tmp_path), "fix a race", claude="/bin/claude", parent="sess-9")
    assert launched(f)[:4] == ["/bin/claude", "--model", models.OPUS_5_5, "--"]
    assert launched(f)[4].startswith("fix a race When finished call SendMessage to sess-9")
    assert "model=claude-opus-5-5" in capsys.readouterr().err
    f.calls.clear()
    plain_tab(ad, str(tmp_path), "fix a race", claude="/bin/claude", model="claude-sonnet-5", parent="sess-9")
    assert launched(f)[2] == "claude-sonnet-5"


def test_recording_failure_is_swallowed(tmp_path):
    record_model_decision(tmp_path / "no" / "such" / "dir" / "x" / "\0", "abc", "m", "s")


def test_kitty_open_forwards_model_as_a_cli_flag():
    seen = {}
    def runner(argv, **k):
        seen["argv"] = argv
        return subprocess.CompletedProcess(argv, 0, json.dumps({"status": "opened"}), "")
    kitty_open.open_task("abc", "/w", runner=runner, env={}, prompt="p", model="claude-opus-5")
    assert seen["argv"][-2:] == ["--model", "claude-opus-5"] or "--model" in seen["argv"]
    seen.clear()
    kitty_open.open_task("abc", "/w", runner=runner, env={})
    assert "--model" not in seen["argv"]


def test_caller_reason_is_recorded_verbatim_not_a_rule_string(tmp_path):
    lau, st, f = make(tmp_path, model="claude-opus-5-5", model_reason="cross-module race, needs depth",
                      task_model=lambda tid: (models.SONNET_5, "small items=1"))
    lau.start("abc", str(tmp_path))
    assert lau.chosen == ("claude-opus-5-5", "caller: cross-module race, needs depth")
    assert "--model" in launched(f)


def test_recorded_decision_carries_the_callers_reason(tmp_path):
    from taskfw.task import Task
    from taskfw.store import TaskStore
    s = TaskStore(tmp_path / "t.db")
    tid = s.save(Task(title="x")).id
    record_model_decision(tmp_path / "t.db", tid, "claude-opus-5", "caller: needs depth")
    assert [e["text"] for e in s.events(tid) if e["kind"] == "decision"] == [
        "kitty tab model: claude-opus-5 (caller: needs depth)"]


def test_omitted_model_is_flagged_as_fallback_and_never_blocks(tmp_path, monkeypatch):
    from taskfw import mcp_server as m
    from taskfw.store import TaskStore
    from taskfw.task import Task
    s = TaskStore(tmp_path / "t.db"); m.set_store(s)
    try:
        tid = s.save(Task(title="x")).id
        seen = {}
        def fake(tid, wt, prompt="", isolate=None, model="", model_reason=""):
            seen.update(model=model, reason=model_reason)
            return {"status": "opened", "model": "claude-sonnet-5", "model_source": "small items=0"}
        monkeypatch.setattr(m.kitty_open, "open_task", fake)
        out = m.tasks__open_in_kitty(tid, worktree=str(tmp_path))
        assert out["status"] == "opened" and "fallback rules" in out["model_note"]
        out = m.tasks__open_in_kitty(tid, worktree=str(tmp_path), model="claude-opus-5", model_reason="why")
        assert "model_note" not in out and seen == {"model": "claude-opus-5", "reason": "why"}
    finally:
        m.set_store(None)


def test_kitty_open_forwards_model_reason():
    seen = {}
    def runner(argv, **k):
        seen["argv"] = argv
        return subprocess.CompletedProcess(argv, 0, json.dumps({"status": "opened", "model": "m",
                                                                 "model_source": "caller: r"}), "")
    out = kitty_open.open_task("abc", "/w", runner=runner, env={}, model="claude-opus-5", model_reason="r")
    assert seen["argv"][-4:] == ["--model", "claude-opus-5", "--model-reason", "r"]
    assert out["model_source"] == "caller: r"
    kitty_open.open_task("abc", "/w", runner=runner, env={}, model_reason="orphan")
    assert "--model-reason" not in seen["argv"]
