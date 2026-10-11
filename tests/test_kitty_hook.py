import io
import json
import sqlite3
import subprocess

from taskfw.kitty.adapter import KittyAdapter
from taskfw.kitty.hook import handle, load_context, main
from taskfw.kitty.store import Store

ENV = {"TASKFW_TASK_ID": "abc", "KITTY_LISTEN_ON": "unix:/tmp/s", "KITTY_WINDOW_ID": "7"}


def fake_adapter():
    calls = []

    def run(argv):
        calls.append(list(argv))
        return subprocess.CompletedProcess([], 0, "", "")

    return KittyAdapter("unix:/tmp/s", kitten="/k/kitten", runner=run, check_version=False), calls


def make_db(path):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY, status TEXT, title TEXT, data TEXT)")
    data = {"resolution": [{"done": True, "text": "one"}, {"done": False, "text": "two"}]}
    con.execute("INSERT INTO tasks VALUES ('abc','open','Do it',?)", (json.dumps(data),))
    con.commit()
    con.close()


def test_noop_without_task_id():
    ad, calls = fake_adapter()
    assert handle({"hook_event_name": "Stop"}, {"KITTY_WINDOW_ID": "7"}, adapter=ad) is None
    assert calls == []


def test_state_per_event():
    for ev, state in [("SessionStart", "waiting"), ("UserPromptSubmit", "running"),
                      ("Stop", "waiting"), ("Notification", "waiting"), ("SessionEnd", "done")]:
        ad, calls = fake_adapter()
        handle({"hook_event_name": ev}, ENV, adapter=ad, store=Store(":memory:"))
        assert calls[-1][calls[-1].index("--match"):][:3] == ["--match", "id:7", f"state={state}"]
        stamped = any(a.startswith("last_active=") for a in calls[-1])
        assert any(a == "prompted=1" for a in calls[-1]) == (ev == "UserPromptSubmit")
        assert stamped == (ev in {"SessionStart", "UserPromptSubmit", "Stop"})  # not Notification


def test_session_start_backfills_matching_window():
    st = Store(":memory:")
    st.record_launch(task_id="abc", window_id=7, tab_id=3, socket="unix:/tmp/s", cwd="/w")
    ad, _ = fake_adapter()
    handle({"hook_event_name": "SessionStart", "session_id": "sess-1"}, ENV, adapter=ad, store=st)
    assert st.latest_session_for_task("abc") == "sess-1"


def test_socket_without_prefix_still_matches():
    st = Store(":memory:")
    st.record_launch(task_id="abc", window_id=7, tab_id=3, socket="unix:/tmp/s", cwd="/w")
    ad, _ = fake_adapter()
    env = {**ENV, "KITTY_LISTEN_ON": "/tmp/s"}
    handle({"hook_event_name": "SessionStart", "session_id": "s2"}, env, adapter=ad, store=st)
    assert st.latest_session_for_task("abc") == "s2"


def test_prompt_submit_injects_context(tmp_path):
    db = str(tmp_path / "t.db")
    make_db(db)
    ad, _ = fake_adapter()
    out = handle({"hook_event_name": "UserPromptSubmit"}, {**ENV, "TASKFW_DB": db}, adapter=ad)
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "Task abc [open]: Do it" in ctx and "1/2" in ctx and "- [ ] two" in ctx
    assert "one" not in ctx.replace("Do it", "")


def test_unknown_task_gives_no_context(tmp_path):
    db = str(tmp_path / "t.db")
    make_db(db)
    assert load_context(db, "nope") is None


def test_main_fails_open_on_bad_input_and_missing_db(capsys):
    assert main(io.StringIO("not json"), {}) == 0
    ad_env = {"TASKFW_TASK_ID": "abc", "TASKFW_DB": "/nonexistent/x.db"}  # no kitty vars
    assert main(io.StringIO(json.dumps({"hook_event_name": "UserPromptSubmit"})), ad_env) == 0
