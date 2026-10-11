import os
import subprocess
from pathlib import Path

import pytest

from taskfw.kitty.adapter import (KittyAdapter, KittyError, KittyUnreachable,
                                  KittyVersionMismatch, parse_ls)

FIXTURE = Path(__file__).parent / "fixtures" / "ls_0_49_2.json"


def cp(rc=0, out="", err=""):
    return subprocess.CompletedProcess([], rc, out, err)


class Fake:
    def __init__(self, *results):
        self.results, self.calls = list(results), []

    def __call__(self, argv):
        self.calls.append(list(argv))
        return self.results.pop(0)


def adapter(*results):
    f = Fake(*results)
    return KittyAdapter("/tmp/s", kitten="/k/kitten", runner=f, check_version=False), f


def test_parse_live_fixture():
    wins = parse_ls(FIXTURE.read_text())
    assert wins and all(isinstance(w.id, int) and isinstance(w.user_vars, dict) for w in wins)


def test_parse_minimal_and_null_fields():
    raw = [{"id": 1, "tabs": [{"id": 2, "windows": [{"id": 3, "env": None, "user_vars": None,
                                                     "cwd": None}]}]}]
    (w,) = parse_ls(raw)
    assert (w.id, w.tab_id, w.os_window_id, w.env, w.user_vars, w.cwd) == (3, 2, 1, {}, {}, "")
    assert parse_ls([{"id": 1, "tabs": None}]) == []


def test_empty_match_is_empty_list():
    a, _ = adapter(cp(1, err="No matching windows for expression: var:task_id=x"))
    assert a.find_by_var("task_id", "x") == []


def test_connect_failure_is_typed():
    a, _ = adapter(cp(1, err="Failed to connect to unix:/tmp/s: no such file or directory"))
    with pytest.raises(KittyUnreachable):
        a.ls()


def test_other_failure_is_generic():
    a, _ = adapter(cp(1, err="boom"))
    with pytest.raises(KittyError) as e:
        a.ls()
    assert not isinstance(e.value, KittyUnreachable)


def test_launch_argv_is_a_list_and_preserves_awkward_values():
    a, f = adapter(cp(0, "42\n"))
    wid = a.launch(cwd="/tmp/my dir", env={"X": "a=b'\"c"}, user_vars={"task_id": "t1"},
                   cmd=["claude", "--settings", "/p q/h.json"], title="t")
    assert wid == 42
    argv = f.calls[0]
    assert argv[:4] == ["/k/kitten", "@", "--to", "unix:/tmp/s"]
    assert "--cwd=/tmp/my dir" in argv
    assert argv[argv.index("--env") + 1] == "X=a=b'\"c"
    assert argv[argv.index("--var") + 1] == "task_id=t1"
    assert argv[argv.index("--") + 1:] == ["claude", "--settings", "/p q/h.json"]


def test_launch_bad_output():
    a, _ = adapter(cp(0, "nope"))
    with pytest.raises(KittyError):
        a.launch(cwd="/")


def test_set_user_vars_targets_window_id():
    a, f = adapter(cp(0))
    a.set_user_vars(7, {"state": "waiting"})
    assert f.calls[0][4:] == ["set-user-vars", "--match", "id:7", "state=waiting"]
    a.set_user_vars(7, {})  # no-op, no call
    assert len(f.calls) == 1


def test_version_check():
    f = Fake(cp(0, "kitten 0.49.2 created by Kovid Goyal\n"), cp(0, "kitten 0.50.0 x\n"))
    KittyAdapter("/tmp/s", kitten="/k", runner=f)
    with pytest.raises(KittyVersionMismatch):
        KittyAdapter("/tmp/s", kitten="/k", runner=f)


@pytest.mark.skipif(not os.environ.get("KITTY_TEST_SOCKET"), reason="set KITTY_TEST_SOCKET")
def test_live_launch_roundtrip():
    a = KittyAdapter(os.environ["KITTY_TEST_SOCKET"])
    wid = a.launch(cwd="/tmp", env={"TASKFW_T": "a=b'c"}, user_vars={"task_id": "adapter-it"},
                   cmd=["/bin/sh", "-c", "sleep 30"])
    try:
        (w,) = a.find_by_var("task_id", "adapter-it")
        assert w.id == wid  # launch returns the window id; ls-by-var agrees
        assert w.env.get("TASKFW_T") == "a=b'c"
    finally:
        subprocess.run([a.kitten, "@", "--to", a.socket, "close-tab", "--match", f"id:{wid}"])


def test_launch_passes_watcher_only_when_given():
    a, f = adapter(cp(0, "1\n"), cp(0, "2\n"))
    a.launch(cwd="/", watcher="/w/watcher.py")
    a.launch(cwd="/")
    assert "--watcher=/w/watcher.py" in f.calls[0]
    assert not any(x.startswith("--watcher") for x in f.calls[1])
