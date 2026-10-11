import subprocess

import pytest

from taskfw.kitty import worktrees as w
from taskfw.kitty.launcher import resolve_worktree


def git(cwd, *a):
    subprocess.run(["git", "-C", str(cwd), *a], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "proj"
    r.mkdir()
    git(r, "init", "-b", "main")
    git(r, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "--allow-empty", "-m", "init")
    return r


def commit(cwd):
    (cwd / "f").write_text("x")
    git(cwd, "add", "f")
    git(cwd, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "work")


def test_path_derived_from_task_id_so_tasks_cannot_collide(repo):
    a, b = w.ensure(repo, "aaa111"), w.ensure(repo, "bbb222")
    assert a != b and a.is_dir() and b.is_dir() and repo not in a.parents


def test_ensure_is_idempotent(repo):
    assert w.ensure(repo, "aaa111") == w.ensure(repo, "aaa111")


def test_unsafe_task_id_refused(repo):
    with pytest.raises(w.WorktreeError):
        w.ensure(repo, "../x")


def test_repo_root_from_inside_a_worktree_is_the_main_checkout(repo):
    assert w.repo_root(w.ensure(repo, "aaa111")) == repo.resolve()


def test_remove_refuses_dirty_and_unmerged_then_removes_clean(repo):
    p = w.ensure(repo, "aaa111")
    (p / "scratch").write_text("u")
    with pytest.raises(w.WorktreeError, match="uncommitted"):
        w.remove(repo, "aaa111")
    (p / "scratch").unlink()
    commit(p)
    with pytest.raises(w.WorktreeError, match="not in HEAD"):
        w.remove(repo, "aaa111")
    git(repo, "merge", "task/aaa111")
    assert "removed" in w.remove(repo, "aaa111") and not p.exists()


def test_resolve_default_is_cwd_and_creates_nothing(repo, tmp_path, monkeypatch):
    monkeypatch.chdir(repo)
    assert resolve_worktree("t1", None) == str(repo.resolve())
    assert not (tmp_path / "proj-worktrees").exists()
    assert "task/t1" not in subprocess.run(["git", "-C", str(repo), "branch"], capture_output=True, text=True).stdout


def test_resolve_isolate_creates_then_reuses_and_non_repo_falls_back(repo, tmp_path, monkeypatch):
    plain = tmp_path / "plain"
    plain.mkdir()
    monkeypatch.chdir(plain)
    assert resolve_worktree("t1", None, isolate=True) == str(plain.resolve())
    monkeypatch.chdir(repo)
    first = resolve_worktree("t1", None, isolate=True)
    assert first.endswith("proj-worktrees/t1") and resolve_worktree("t1", None, isolate=True) == first


def test_resolve_explicit_path_wins_even_with_isolate(repo, monkeypatch):
    monkeypatch.chdir(repo)
    assert resolve_worktree("t1", "/some/dir") == "/some/dir"
    assert resolve_worktree("t1", "/some/dir", isolate=True) == "/some/dir"


def _main_start(monkeypatch, repo, argv):
    from taskfw.kitty import launcher
    seen = {}

    class FakeLauncher:
        def __init__(self, *a, **k): pass
        def start(self, task_id, wt): seen["start"] = wt; return 1
        def resume(self, task_id, wt, fresh): seen["resume"] = wt; return 1

    monkeypatch.chdir(repo)
    monkeypatch.setattr(launcher, "Launcher", FakeLauncher)
    monkeypatch.setattr(launcher, "KittyAdapter", lambda s: None)
    monkeypatch.setattr(launcher, "check_install", lambda: None)
    rc = launcher.main(["--socket", "unix:/x", "--store", ":memory:", *argv], env={})
    return rc, seen


def test_main_start_default_isolate_and_removed_flag(repo, tmp_path, monkeypatch):
    rc, seen = _main_start(monkeypatch, repo, ["start", "t1"])
    assert rc == 0 and seen["start"] == str(repo.resolve()) and not (tmp_path / "proj-worktrees").exists()
    rc, seen = _main_start(monkeypatch, repo, ["start", "t1", "--isolate"])
    assert seen["start"].endswith("proj-worktrees/t1")
    rc, seen = _main_start(monkeypatch, repo, ["start", "t2", "--isolate", "--worktree", "/some/dir"])
    assert seen["start"] == "/some/dir"
    with pytest.raises(SystemExit):
        _main_start(monkeypatch, repo, ["start", "t1", "--no-worktree"])


def test_main_open_default_and_isolate(repo, tmp_path, monkeypatch, capsys):
    from taskfw.kitty import launcher, opener
    got = []
    monkeypatch.chdir(repo)
    monkeypatch.setattr(opener, "open_task", lambda t, wt, **k: got.append(wt) or opener._not_opened("x"))
    launcher.main(["open", "t1"], env={})
    launcher.main(["open", "t1", "--isolate"], env={})
    assert got[0] == str(repo.resolve()) and got[1].endswith("proj-worktrees/t1")


def test_resume_keeps_stored_worktree_and_never_creates_one(repo, tmp_path, monkeypatch):
    wt = w.ensure(repo, "t9")
    rc, seen = _main_start(monkeypatch, repo, ["resume", "t9", "--worktree", str(wt)])
    assert seen["resume"] == str(wt)
    rc, seen = _main_start(monkeypatch, repo, ["resume", "t8"])
    assert seen["resume"] is None and not (tmp_path / "proj-worktrees" / "t8").exists()
