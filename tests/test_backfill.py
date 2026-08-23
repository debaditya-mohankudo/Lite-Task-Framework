"""Backfill — recovering commit links from git history.

There is no automatic capture (see taskfw/backfill.py's docstring for why),
so this is the only mechanism that can repair a missed tasks__add_commit call,
and the property it protects is that it never depends on that call having
happened.
"""
from __future__ import annotations

import subprocess

import pytest

from taskfw.backfill import backfill, extract_task_ids, git_log
from taskfw.task import Task
from taskfw.store import TaskStore


@pytest.fixture
def store(tmp_path):
    s = TaskStore(tmp_path / "t.db")
    yield s
    s.close()


@pytest.fixture
def repo(tmp_path):
    """A real git repo — backfill shells out to git log, so faking it proves little."""
    path = tmp_path / "repo"
    path.mkdir()
    run = lambda *a: subprocess.run(a, cwd=path, capture_output=True, check=True)  # noqa: E731
    run("git", "init", "-q")
    run("git", "config", "user.email", "t@example.com")
    run("git", "config", "user.name", "Test")
    (path / "f.txt").write_text("hello")
    run("git", "add", "-A")
    return path


def commit(repo, message: str) -> str:
    (repo / "f.txt").write_text(message)
    subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=repo, capture_output=True, check=True)
    out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
    return out.stdout.strip()


class TestTaskRefExtraction:
    def test_finds_a_reference(self):
        assert extract_task_ids("fix thing\n\ntask:abc123") == ["abc123"]

    def test_finds_several_and_dedupes(self):
        assert extract_task_ids("task:aaa111 task:bbb222 task:aaa111") == ["aaa111", "bbb222"]

    def test_is_case_insensitive_and_normalises(self):
        assert extract_task_ids("TASK:ABC123") == ["abc123"]

    def test_ignores_non_references(self):
        assert extract_task_ids("no refs here") == []
        assert extract_task_ids("task:xy") == []  # too short to be an id

    def test_handles_empty_input(self):
        assert extract_task_ids("") == []
        assert extract_task_ids(None) == []


class TestBackfill:
    def test_recovers_a_link_nothing_ever_made(self, store, repo):
        t = store.save(Task(title="t"))
        sha = commit(repo, f"landed with no tasks__add_commit call\n\ntask:{t.id}")
        assert store.commits(t.id) == []
        result = backfill(str(repo), store=store)
        assert result["linked"] == 1
        assert [c["sha"] for c in store.commits(t.id)] == [sha]

    def test_is_idempotent(self, store, repo):
        t = store.save(Task(title="t"))
        commit(repo, f"task:{t.id}")
        backfill(str(repo), store=store)
        second = backfill(str(repo), store=store)
        assert second["linked"] == 0 and second["already_linked"] == 1

    def test_dry_run_writes_nothing(self, store, repo):
        t = store.save(Task(title="t"))
        commit(repo, f"task:{t.id}")
        result = backfill(str(repo), dry_run=True, store=store)
        assert result["linked"] == 1
        assert store.commits(t.id) == []

    def test_counts_references_to_unknown_tasks(self, store, repo):
        commit(repo, "task:deadbeef")
        assert backfill(str(repo), store=store)["unknown_tasks"] == 1

    def test_non_git_directory_is_not_an_error(self, store, tmp_path):
        assert backfill(str(tmp_path), store=store)["scanned"] == 0

    def test_git_log_parses_multiline_messages(self, repo):
        commit(repo, "subject line\n\nbody with task:abc123 inside")
        entries = git_log(str(repo))
        assert any("abc123" in message for _, message in entries)
