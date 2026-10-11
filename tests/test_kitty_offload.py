"""Offload of small prompts to a cheap-model side window (task:125d60bd)."""
import subprocess

import pytest

from taskfw.kitty import offload
from taskfw.kitty.adapter import KittyAdapter, KittyUnreachable
from taskfw.kitty.hook import handle


@pytest.mark.parametrize("prompt", [
    "what is the default port?", "where is the store defined?", "which task owns this tab?",
    "how many tests are there?", "who wrote the watcher?", "when was the last release?",
    "what's the pinned kitty version?",
])
def test_small_lookups_offload(prompt):
    assert offload.is_small(prompt)


@pytest.mark.parametrize("prompt", [
    "", "yes", "ok go ahead", "continue", "no, that's wrong", "do it",          # replies to the main session
    "/model haiku", "!ls", "# note",                                           # commands
    "fix the watcher", "add a test for hook.py", "what does src/taskfw.kitty/hook.py do?",  # edits / files
    "why does the watcher drop events?", "explain the design", "should we use hsplit?",
    "what is this ```x```", "what is https://example.com", "run the tests",
    "what is the default port?\nand also fix it",                              # multi-line
    "what " + "word " * 25,                                                    # too long
])
def test_everything_else_stays_in_main_session(prompt):
    assert not offload.is_small(prompt)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(offload, "DISABLE_FILE", tmp_path / "off")
    monkeypatch.setattr(offload.shutil, "which", lambda *a, **k: "/bin/claude")
    t = tmp_path / "t.jsonl"
    t.write_text("{}")
    calls = []

    def run(argv):
        calls.append(list(argv))
        return subprocess.CompletedProcess([], 0, "42\n", "")

    ad = KittyAdapter("unix:/tmp/s", kitten="/k/kitten", runner=run, check_version=False)
    env = {"KITTY_LISTEN_ON": "unix:/tmp/s", "KITTY_WINDOW_ID": "7", "PATH": "/bin"}
    payload = {"hook_event_name": "UserPromptSubmit", "prompt": "what is the default port?",
               "session_id": "sess-1", "transcript_path": str(t), "cwd": "/w"}
    return ad, calls, env, payload, tmp_path


def test_offload_blocks_and_launches_forked_haiku_split(setup):
    ad, calls, env, payload, _ = setup
    out = handle(payload, env, adapter=ad)
    assert out["decision"] == "block" and "window 42" in out["reason"]
    argv = calls[0]
    assert "--type=window" in argv and "--location=hsplit" in argv and "--next-to=id:7" in argv
    assert "--keep-focus" in argv and "--hold" in argv
    cmd = argv[argv.index("--") + 1:]
    assert cmd == ["/bin/claude", "-p", "--model", "claude-haiku-5-5", "--resume", "sess-1",
                   "--fork-session", "--output-format", "text", "what is the default port?"]
    assert f"{offload.OFFLOAD_CHILD_ENV}=1" in argv


@pytest.mark.parametrize("mutate", [
    lambda p, e: p.update(prompt="fix the watcher"),
    lambda p, e: p.update(hook_event_name="Stop"),
    lambda p, e: e.update({offload.OFFLOAD_CHILD_ENV: "1"}),       # loop guard
    lambda p, e: e.pop("KITTY_LISTEN_ON"),
    lambda p, e: e.update(KITTY_WINDOW_ID="x"),
    lambda p, e: p.update(session_id=""),
    lambda p, e: p.update(transcript_path="/nonexistent"),
])
def test_no_offload_leaves_prompt_to_main_session(setup, mutate):
    ad, calls, env, payload, _ = setup
    mutate(payload, env)
    assert handle(payload, env, adapter=ad) is None
    assert calls == []


def test_kill_switch_file(setup):
    ad, calls, env, payload, tmp = setup
    (tmp / "off").touch()
    assert handle(payload, env, adapter=ad) is None and calls == []


def test_oversized_transcript_is_not_forked(setup, monkeypatch):
    ad, calls, env, payload, _ = setup
    monkeypatch.setattr(offload, "MAX_TRANSCRIPT_BYTES", 1)
    assert handle(payload, env, adapter=ad) is None and calls == []


def test_missing_claude_binary(setup, monkeypatch):
    ad, calls, env, payload, _ = setup
    monkeypatch.setattr(offload.shutil, "which", lambda *a, **k: None)
    assert handle(payload, env, adapter=ad) is None and calls == []


def test_launch_failure_fails_open_not_blocked(setup):
    _, _, env, payload, _ = setup

    def boom(argv):
        raise KittyUnreachable("down")

    ad = KittyAdapter("unix:/tmp/s", kitten="/k/kitten", runner=boom, check_version=False)
    assert handle(payload, env, adapter=ad) is None


def test_launch_nonzero_exit_fails_open_not_blocked(setup):
    _, _, env, payload, _ = setup
    ad = KittyAdapter("unix:/tmp/s", kitten="/k/kitten", check_version=False,
                      runner=lambda argv: subprocess.CompletedProcess([], 1, "", "boom"))
    assert handle(payload, env, adapter=ad) is None
