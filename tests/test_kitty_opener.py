import json
import subprocess

import pytest

from taskfw.kitty import adapter as adapter_mod
from taskfw.kitty.adapter import KittyAdapter, KittyUnreachable
from taskfw.kitty.launcher import main
from taskfw.kitty.opener import open_task
from taskfw.kitty.store import Store


def cp(rc=0, out="", err=""):
    return subprocess.CompletedProcess([], rc, out, err)


def ls_json(*wins):  # wins: (tab_id, window_id, user_vars)
    return json.dumps([{"id": 1, "tabs": [{"id": t, "windows": [{"id": w, "user_vars": v}]}
                                          for (t, w, v) in wins]}])


class World:
    """Fake kittys keyed by socket; every kitten call is recorded and nothing is live."""
    def __init__(self, **kittys):
        self.kittys, self.calls = kittys, []   # socket -> list of (tab, win, vars), or None=down

    def factory(self, sock):
        def run(argv):
            self.calls.append(list(argv))
            to = argv[argv.index("--to") + 1] if "--to" in argv else sock
            wins = self.kittys.get(to)
            if wins is None:
                return cp(1, err=f"Failed to connect to {to}: no such file or directory")
            if "launch" in argv:
                return cp(out="42\n")
            if "ls" in argv:
                if "--match" in argv:
                    return cp(out=ls_json((9, 42, {"task_id": "abc"})))
                return cp(out=ls_json(*wins) if wins else "[]")
            return cp()
        return KittyAdapter(sock, kitten="/k/kitten", runner=run, check_version=False)

    def launches(self):
        return [c for c in self.calls if "launch" in c]


@pytest.fixture
def ctx(tmp_path):
    (tmp_path / "wt").mkdir()
    return dict(worktree=str(tmp_path / "wt"), store=str(tmp_path / "s.sqlite"),
                env={"PATH": "/bin", "KITTY_LISTEN_ON": ""}, claude="/bin/claude",
                socket_glob=str(tmp_path / "none-*"))


def run(world, ctx, **kw):
    args = {**ctx, "adapter_factory": world.factory, **kw}
    return open_task("abc", args.pop("worktree"), **args)


def test_opens_a_new_tab_and_reports_the_window(ctx):
    w = World(**{"unix:/tmp/kitty-1": []})
    r = run(w, ctx, socket="/tmp/kitty-1")
    assert (r.status, r.window_id, r.socket) == ("opened", 42, "unix:/tmp/kitty-1")
    argv = w.launches()[0]
    assert "TASKFW_TASK_ID=abc" in argv and "task_id=abc" in argv  # same as `start`
    assert Store(ctx["store"]).latest_for_task("abc").window_id == 42


def test_live_tab_for_the_task_means_exists_and_no_launch(ctx):
    w = World(**{"unix:/tmp/kitty-1": [(3, 7, {"task_id": "abc"})]})
    r = run(w, ctx, socket="/tmp/kitty-1")
    assert (r.status, r.window_id) == ("exists", 7)
    assert w.launches() == []


def test_exists_is_found_in_another_kitty_than_the_preferred_one(ctx, tmp_path):
    for n in (1, 2):
        (tmp_path / f"k-{n}").touch()
    w = World(**{f"unix:{tmp_path}/k-1": [], f"unix:{tmp_path}/k-2": [(3, 7, {"task_id": "abc"})]})
    r = run(w, ctx, socket_glob=str(tmp_path / "k-*"),
            env={**ctx["env"], "KITTY_LISTEN_ON": f"unix:{tmp_path}/k-1"})
    assert (r.status, r.window_id, r.socket) == ("exists", 7, f"unix:{tmp_path}/k-2")
    assert w.launches() == []


def test_dead_store_row_is_not_exists_and_points_to_resume(ctx):
    st = Store(ctx["store"])
    st.record_launch(task_id="abc", window_id=5, tab_id=1, socket="unix:/tmp/kitty-1", cwd="/x")
    w = World(**{"unix:/tmp/kitty-1": []})
    r = run(w, ctx, socket="/tmp/kitty-1")
    assert r.status == "not_opened" and "resume abc" in r.reason
    assert w.launches() == []


def test_hibernated_task_is_never_resumed_or_restarted_by_open(ctx):
    st = Store(ctx["store"])
    rid = st.record_launch(task_id="abc", window_id=5, tab_id=1, socket="unix:/tmp/kitty-1", cwd="/x")
    st.backfill_session(socket="unix:/tmp/kitty-1", window_id=5, session_id="s1")
    st.mark_closed(rid, "hibernated")
    w = World(**{"unix:/tmp/kitty-1": []})
    r = run(w, ctx, socket="/tmp/kitty-1")
    assert r.status == "not_opened" and "resume abc" in r.reason and "--fresh" not in r.reason
    assert w.launches() == []


def test_unreachable_socket_is_not_opened_with_reason(ctx):
    r = run(World(**{"unix:/tmp/kitty-1": None}), ctx, socket="/tmp/kitty-1")
    assert r.status == "not_opened" and "Failed to connect" in r.reason


def test_no_socket_anywhere(ctx):
    r = run(World(), ctx)
    assert r.status == "not_opened" and "no kitty socket" in r.reason


def test_several_live_kittys_without_a_choice_is_ambiguous_and_launches_nothing(ctx, tmp_path):
    for n in (1, 2):
        (tmp_path / f"k-{n}").touch()
    w = World(**{f"unix:{tmp_path}/k-1": [], f"unix:{tmp_path}/k-2": []})
    r = run(w, ctx, socket_glob=str(tmp_path / "k-*"))
    assert r.status == "not_opened" and "2 kitty instances" in r.reason
    assert w.launches() == []


def test_kitty_listen_on_breaks_a_tie(ctx, tmp_path):
    for n in (1, 2):
        (tmp_path / f"k-{n}").touch()
    w = World(**{f"unix:{tmp_path}/k-1": [], f"unix:{tmp_path}/k-2": []})
    r = run(w, ctx, socket_glob=str(tmp_path / "k-*"),
            env={**ctx["env"], "KITTY_LISTEN_ON": f"unix:{tmp_path}/k-2"})
    assert (r.status, r.socket) == ("opened", f"unix:{tmp_path}/k-2")


def test_unsupported_kitty_version_is_not_opened(ctx):
    def factory(sock):
        return KittyAdapter(sock, kitten="/k/kitten", check_version=True,
                            runner=lambda argv: cp(out="kitten 0.99.0 created by Kovid"))
    r = open_task("abc", ctx["worktree"], socket="/tmp/kitty-1", adapter_factory=factory,
                  **{k: ctx[k] for k in ("store", "env", "claude", "socket_glob")})
    assert r.status == "not_opened" and "unsupported kitty" in r.reason


def test_missing_kitten_binary_does_not_raise(ctx, monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("/nope/kitten")
    monkeypatch.setattr(adapter_mod.subprocess, "run", boom)
    r = open_task("abc", ctx["worktree"], socket="/tmp/kitty-1", env=ctx["env"], store=ctx["store"],
                  claude="/bin/claude", adapter_factory=lambda s: KittyAdapter(s, kitten="/nope/kitten"))
    assert r.status == "not_opened" and "cannot run kitten" in r.reason


def test_hung_kitten_times_out_instead_of_hanging(monkeypatch):
    def hang(*a, **k):
        raise subprocess.TimeoutExpired("kitten", 20)
    monkeypatch.setattr(adapter_mod.subprocess, "run", hang)
    with pytest.raises(KittyUnreachable, match="did not answer"):
        adapter_mod._run(["kitten", "@", "ls"])


def test_missing_worktree_or_claude_launches_nothing(ctx, tmp_path):
    w = World(**{"unix:/tmp/kitty-1": []})
    assert "not a directory" in run(w, ctx, worktree=str(tmp_path / "nope"), socket="/tmp/kitty-1").reason
    r = run(w, ctx, socket="/tmp/kitty-1", claude="")
    assert r.status == "not_opened" and "claude not found" in r.reason
    assert w.launches() == []


def test_anything_unexpected_still_returns_a_result(ctx):
    def factory(sock):
        raise RuntimeError("surprise")
    r = open_task("abc", ctx["worktree"], socket="/tmp/kitty-1", adapter_factory=factory,
                  **{k: ctx[k] for k in ("store", "env", "claude", "socket_glob")})
    assert r.status == "not_opened" and "RuntimeError" in r.reason


def test_cli_prints_json_and_exits_zero_when_not_opened(capsys, tmp_path):
    rc = main(["--store", str(tmp_path / "s.sqlite"), "open", "abc", "--worktree", str(tmp_path)],
              env={"PATH": "/bin:/usr/bin", "KITTY_LISTEN_ON": "unix:/nonexistent/kitty"})
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["status"] == "not_opened" and out["reason"]


def test_prompt_reaches_claude_argv_only_when_given(ctx):
    w = World(**{"unix:/tmp/kitty-1": []})
    run(w, ctx, socket="/tmp/kitty-1", prompt="go: read notes")
    assert w.launches()[0][-2:] == ["--", "go: read notes"]
    w2 = World(**{"unix:/tmp/kitty-1": []})
    run(w2, {**ctx, "store": ctx["store"] + "2"}, socket="/tmp/kitty-1")
    assert "go: read notes" not in w2.launches()[0] and w2.launches()[0].count("--") == 1


# --- isolation (task:d482a160) -------------------------------------------------------------

def _git(cwd, *a):
    subprocess.run(["git", "-C", str(cwd), *a], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "proj"
    r.mkdir()
    _git(r, "init", "-b", "main")
    _git(r, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "--allow-empty", "-m", "init")
    return r


def _sibling(ctx, cwd, window_id=7, closed=False):
    st = Store(ctx["store"])
    row = st.record_launch(task_id="sib", window_id=window_id, tab_id=1, socket="unix:/tmp/kitty-1", cwd=str(cwd))
    if closed:
        st.mark_closed(row)
    st.db.close()


def _cwd_of(w):
    return next(a for a in w.launches()[0] if a.startswith("--cwd=")).removeprefix("--cwd=")


def _open(ctx, repo, wins, **kw):
    w = World(**{"unix:/tmp/kitty-1": wins})
    return w, run(w, {**ctx, "worktree": str(repo)}, socket="/tmp/kitty-1", **kw)


def test_lone_tab_keeps_the_plain_directory(ctx, repo):
    _, r = _open(ctx, repo, [])
    assert (r.status, r.isolated, r.worktree) == ("opened", False, str(repo))


def test_live_sibling_in_the_same_repo_isolates_by_default_and_says_so(ctx, repo):
    _sibling(ctx, repo)
    w, r = _open(ctx, repo, [(3, 7, {"task_id": "sib"})])
    assert r.status == "opened" and r.isolated is True
    assert r.worktree.endswith("proj-worktrees/abc") and "sib" in r.reason
    assert _cwd_of(w) == r.worktree


def test_sibling_already_in_a_worktree_still_counts_as_the_same_repo(ctx, repo):
    from taskfw.kitty import worktrees
    _sibling(ctx, worktrees.ensure(repo, "sib"))
    assert _open(ctx, repo, [(3, 7, {"task_id": "sib"})])[1].isolated is True


@pytest.mark.parametrize("case", ["closed", "vanished", "other-repo"])
def test_closed_row_or_vanished_window_or_other_repo_is_not_a_sibling(ctx, repo, tmp_path, case):
    other = tmp_path / "other"
    other.mkdir()
    _git(other, "init", "-b", "main")
    _sibling(ctx, other if case == "other-repo" else repo, closed=case == "closed")
    _, r = _open(ctx, repo, [] if case == "vanished" else [(3, 7, {})])
    assert (r.status, r.isolated) == ("opened", False)


def test_explicit_true_isolates_a_lone_tab(ctx, repo):
    _, r = _open(ctx, repo, [], isolate=True)
    assert r.isolated is True and r.worktree.endswith("proj-worktrees/abc") and "requested" in r.reason


def test_explicit_false_beats_a_live_sibling(ctx, repo):
    _sibling(ctx, repo)
    _, r = _open(ctx, repo, [(3, 7, {"task_id": "sib"})], isolate=False)
    assert (r.isolated, r.worktree) == (False, str(repo))


def test_isolate_outside_a_repo_falls_back_and_says_so(ctx):
    w = World(**{"unix:/tmp/kitty-1": []})
    r = run(w, ctx, socket="/tmp/kitty-1", isolate=True)
    assert (r.status, r.isolated) == ("opened", False) and "not in a git repository" in r.reason


def test_worktree_failure_is_not_opened_and_launches_nothing(ctx, repo, monkeypatch):
    from taskfw.kitty import worktrees
    monkeypatch.setattr(worktrees, "ensure",
                        lambda *a: (_ for _ in ()).throw(worktrees.WorktreeError("boom")))
    w, r = _open(ctx, repo, [], isolate=True)
    assert r.status == "not_opened" and "boom" in r.reason and not w.launches()
