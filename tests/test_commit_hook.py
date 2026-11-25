"""commit_hook — a Bash `git commit` links HEAD to its cited tasks (task:dd2cf1ec).

Real git repos, because the hook reads HEAD through git; faking it would prove
only that the fake agrees with itself.
"""
from __future__ import annotations

import io
import json
import subprocess
import time

import pytest

from taskfw import commit_hook
from taskfw.commit_hook import commit_dirs, handle, main
from taskfw.store import TaskStore
from taskfw.task import Task


@pytest.fixture
def store(tmp_path):
    s = TaskStore(tmp_path / "t.db")
    yield s
    s.close()


def make_repo(path):
    path.mkdir()
    run = lambda *a: subprocess.run(a, cwd=path, capture_output=True, check=True)  # noqa: E731
    run("git", "init", "-q")
    run("git", "config", "user.email", "t@example.com")
    run("git", "config", "user.name", "Test")
    return path


def git_commit(repo, message: str) -> subprocess.CompletedProcess:
    """Commit a change; returns the process so a test can make it fail."""
    (repo / "f.txt").write_text(message + str(time.time_ns()))
    subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True, check=True)
    return subprocess.run(["git", "commit", "-q", "-m", message], cwd=repo, capture_output=True)


def head(repo) -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True,
                          text=True, check=True).stdout.strip()


def bash(command: str, cwd) -> dict:
    return {"hook_event_name": "PostToolUse", "tool_name": "Bash", "cwd": str(cwd),
            "tool_input": {"command": command}, "tool_response": {"stdout": "", "stderr": ""}}


def task(store, title="t") -> str:
    t = Task(title=title)
    store.save(t)
    return t.id


class TestSuccess:
    def test_commit_citing_a_task_is_linked(self, tmp_path, store):
        repo = make_repo(tmp_path / "r")
        tid = task(store)
        git_commit(repo, f"task:{tid} do the thing")
        assert handle(bash("git commit -m x", repo), store=store) == [tid]
        assert [c["sha"] for c in store.commits(tid)] == [head(repo)]

    def test_every_cited_task_is_linked(self, tmp_path, store):
        repo = make_repo(tmp_path / "r")
        a, b = task(store, "a"), task(store, "b")
        git_commit(repo, f"task:{a} subject\n\nalso task:{b}")
        assert handle(bash("git commit -F msg.txt", repo), store=store) == [a, b]

    def test_cd_into_another_repo_links_that_repo(self, tmp_path, store):
        session, repo = make_repo(tmp_path / "session"), make_repo(tmp_path / "other")
        tid = task(store)
        git_commit(repo, f"task:{tid} elsewhere")
        assert handle(bash(f"cd {repo} && git add -A && git commit -m x", session),
                      store=store) == [tid]

    def test_git_dash_c_links_the_named_repo(self, tmp_path, store):
        session, repo = make_repo(tmp_path / "session"), make_repo(tmp_path / "other")
        tid = task(store)
        git_commit(repo, f"task:{tid} elsewhere")
        assert handle(bash(f"git -C {repo} commit -m x", session), store=store) == [tid]

    def test_linking_twice_is_idempotent(self, tmp_path, store):
        repo = make_repo(tmp_path / "r")
        tid = task(store)
        git_commit(repo, f"task:{tid} once")
        handle(bash("git commit -m x", repo), store=store)
        handle(bash("git commit -m x", repo), store=store)
        assert len(store.commits(tid)) == 1


class TestFailure:
    def test_failed_commit_leaves_old_head_unlinked(self, tmp_path, store):
        repo = make_repo(tmp_path / "r")
        tid = task(store)
        git_commit(repo, f"task:{tid} earlier work")
        later = time.time() + commit_hook.FRESH_SECONDS + 5
        # Nothing staged, so this commit fails and HEAD stays the old commit.
        assert subprocess.run(["git", "commit", "-m", f"task:{tid} again"], cwd=repo,
                              capture_output=True).returncode != 0
        assert handle(bash("git commit -m x", repo), store=store, now=later) == []
        assert store.commits(tid) == []

    def test_failed_commit_right_after_a_good_one_adds_nothing(self, tmp_path, store):
        repo = make_repo(tmp_path / "r")
        tid = task(store)
        git_commit(repo, f"task:{tid} good")
        handle(bash("git commit -m x", repo), store=store)
        subprocess.run(["git", "commit", "-m", "task:ffffff nothing staged"], cwd=repo,
                       capture_output=True)
        handle(bash("git commit -m x", repo), store=store)
        assert len(store.commits(tid)) == 1

    def test_outside_a_repo_does_nothing(self, tmp_path, store):
        assert handle(bash("git commit -m x", tmp_path), store=store) == []

    def test_main_fails_open_on_garbage(self, capsys):
        assert main(io.StringIO("not json")) == 0
        assert capsys.readouterr().out == ""


class TestNoTaskId:
    def test_commit_without_a_task_id_links_nothing(self, tmp_path, store):
        repo = make_repo(tmp_path / "r")
        tid = task(store)
        git_commit(repo, f"plain message mentioning {tid} without the prefix")
        assert handle(bash("git commit -m x", repo), store=store) == []
        assert store.commits(tid) == []

    def test_unknown_task_id_is_skipped(self, tmp_path, store):
        repo = make_repo(tmp_path / "r")
        git_commit(repo, "task:abcdef12 no such task")
        assert handle(bash("git commit -m x", repo), store=store) == []

    def test_non_commit_commands_are_ignored(self, tmp_path, store):
        repo = make_repo(tmp_path / "r")
        tid = task(store)
        git_commit(repo, f"task:{tid} work")
        for cmd in ("git log -1", 'echo "git commit"', "git status && ls"):
            assert handle(bash(cmd, repo), store=store) == []
        assert handle({**bash("git commit", repo), "tool_name": "Read"}, store=store) == []


class TestCommitDirs:
    def test_plain(self):
        assert commit_dirs("git commit -m x", "/a") == ["/a"]

    def test_follows_cd_and_relative_paths(self):
        assert commit_dirs("cd sub && git commit -F m.txt", "/a") == ["/a/sub"]

    def test_value_flags_before_subcommand(self):
        assert commit_dirs("git -c user.name=x commit -m y", "/a") == ["/a"]
        assert commit_dirs("git -C /b -c k=v commit", "/a") == ["/b"]

    def test_env_assignment_prefix(self):
        assert commit_dirs("GIT_EDITOR=true git commit", "/a") == ["/a"]

    def test_echo_is_not_a_commit(self):
        assert commit_dirs('echo "git commit"', "/a") == []
