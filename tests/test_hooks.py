"""Claude Code hook tests, plus the backfill that makes fail-open recoverable.

The property these protect above all: a hook NEVER blocks and NEVER raises.
Every failure path is asserted to return an empty result rather than propagate.
"""
from __future__ import annotations

import json
import subprocess

import pytest

from taskfw.backfill import backfill, git_log
from taskfw.hooks import claude_code as hooks
from taskfw.models import ResolutionItem, Task
from taskfw.store import TaskStore


@pytest.fixture
def store(tmp_path):
    s = TaskStore(tmp_path / "t.db")
    yield s
    s.close()


@pytest.fixture
def repo(tmp_path):
    """A real git repo — commit capture shells out, so faking it proves little."""
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
        assert hooks.extract_task_ids("fix thing\n\ntask:abc123") == ["abc123"]

    def test_finds_several_and_dedupes(self):
        assert hooks.extract_task_ids("task:aaa111 task:bbb222 task:aaa111") == ["aaa111", "bbb222"]

    def test_is_case_insensitive_and_normalises(self):
        assert hooks.extract_task_ids("TASK:ABC123") == ["abc123"]

    def test_ignores_non_references(self):
        assert hooks.extract_task_ids("no refs here") == []
        assert hooks.extract_task_ids("task:xy") == []  # too short to be an id

    def test_handles_empty_input(self):
        assert hooks.extract_task_ids("") == []
        assert hooks.extract_task_ids(None) == []


class TestPointer:
    def test_injects_one_line_naming_the_active_task(self, store):
        t = store.save(Task(title="Build it", resolution=[ResolutionItem("a", True), ResolutionItem("b")]))
        store.set_active(t.id, "/w")
        out = hooks.handle_user_prompt_submit({"cwd": "/w"}, store=store)
        ctx = out["hookSpecificOutput"]["additionalContext"]
        assert t.id in ctx and "Build it" in ctx and "1/2" in ctx
        assert "tasks__context" in ctx
        assert "\n" not in ctx, "the pointer must stay a single line"

    def test_no_active_task_injects_nothing(self, store):
        assert hooks.handle_user_prompt_submit({"cwd": "/w"}, store=store) == {}

    def test_stale_pointer_is_cleared_rather_than_naming_a_ghost(self, store):
        store.set_active("deadbeef", "/w")
        assert hooks.handle_user_prompt_submit({"cwd": "/w"}, store=store) == {}
        assert store.get_active("/w") is None

    def test_scope_comes_from_the_hosts_cwd(self, store):
        t = store.save(Task(title="scoped"))
        store.set_active(t.id, "/one")
        assert hooks.handle_user_prompt_submit({"cwd": "/two"}, store=store) == {}
        assert hooks.handle_user_prompt_submit({"cwd": "/one"}, store=store)

    def test_can_be_disabled_independently(self, store, monkeypatch):
        t = store.save(Task(title="t"))
        store.set_active(t.id, "/w")
        monkeypatch.setenv("TASKFW_HOOK_POINTER", "0")
        assert hooks.handle_user_prompt_submit({"cwd": "/w"}, store=store) == {}


class TestCommitCapture:
    def test_links_a_commit_to_its_task(self, store, repo):
        t = store.save(Task(title="t"))
        sha = commit(repo, f"do the thing\n\ntask:{t.id}")
        hooks.handle_post_tool_use(
            {"tool_name": "Bash", "tool_input": {"command": f'git commit -m "task:{t.id}"'},
             "cwd": str(repo)}, store=store)
        assert [c["sha"] for c in store.commits(t.id)] == [sha]

    def test_ignores_non_bash_tools(self, store):
        hooks.handle_post_tool_use({"tool_name": "Read", "tool_input": {}}, store=store)  # no raise

    def test_ignores_bash_that_is_not_a_commit(self, store, repo):
        t = store.save(Task(title="t"))
        hooks.handle_post_tool_use(
            {"tool_name": "Bash", "tool_input": {"command": f"git status task:{t.id}"},
             "cwd": str(repo)}, store=store)
        assert store.commits(t.id) == []

    def test_untagged_commit_is_simply_not_linked(self, store, repo):
        commit(repo, "no task reference")
        hooks.handle_post_tool_use(
            {"tool_name": "Bash", "tool_input": {"command": 'git commit -m "nothing"'},
             "cwd": str(repo)}, store=store)  # no raise, no link

    def test_reference_to_a_nonexistent_task_is_skipped(self, store, repo):
        commit(repo, "task:deadbeef")
        hooks.handle_post_tool_use(
            {"tool_name": "Bash", "tool_input": {"command": 'git commit -m "task:deadbeef"'},
             "cwd": str(repo)}, store=store)  # no raise

    def test_non_git_cwd_does_not_raise(self, store, tmp_path):
        t = store.save(Task(title="t"))
        hooks.handle_post_tool_use(
            {"tool_name": "Bash", "tool_input": {"command": f'git commit -m "task:{t.id}"'},
             "cwd": str(tmp_path)}, store=store)
        assert store.commits(t.id) == []

    def test_capture_is_idempotent(self, store, repo):
        t = store.save(Task(title="t"))
        commit(repo, f"task:{t.id}")
        payload = {"tool_name": "Bash", "tool_input": {"command": f'git commit -m "task:{t.id}"'},
                   "cwd": str(repo)}
        hooks.handle_post_tool_use(payload, store=store)
        hooks.handle_post_tool_use(payload, store=store)
        assert len(store.commits(t.id)) == 1

    def test_can_be_disabled_independently(self, store, repo, monkeypatch):
        t = store.save(Task(title="t"))
        commit(repo, f"task:{t.id}")
        monkeypatch.setenv("TASKFW_HOOK_COMMITS", "0")
        hooks.handle_post_tool_use(
            {"tool_name": "Bash", "tool_input": {"command": f'git commit -m "task:{t.id}"'},
             "cwd": str(repo)}, store=store)
        assert store.commits(t.id) == []


class TestFailOpen:
    def test_malformed_stdin_still_prints_valid_json(self, monkeypatch, capsys):
        monkeypatch.setattr("sys.stdin", __import__("io").StringIO("not json"))
        assert hooks.main(["UserPromptSubmit"]) == 0
        assert json.loads(capsys.readouterr().out) == {}

    def test_unknown_event_is_a_no_op(self, monkeypatch, capsys):
        monkeypatch.setattr("sys.stdin", __import__("io").StringIO("{}"))
        assert hooks.main(["NoSuchEvent"]) == 0
        assert json.loads(capsys.readouterr().out) == {}

    def test_handler_exception_never_propagates(self, monkeypatch, capsys):
        def boom(payload, store=None):
            raise RuntimeError("handler exploded")

        monkeypatch.setitem(hooks.HANDLERS, "PostToolUse", boom)
        monkeypatch.setattr("sys.stdin", __import__("io").StringIO('{"tool_name":"Bash"}'))
        assert hooks.main(["PostToolUse"]) == 0
        assert json.loads(capsys.readouterr().out) == {}


class TestBackfill:
    def test_recovers_links_the_hook_missed(self, store, repo):
        t = store.save(Task(title="t"))
        sha = commit(repo, f"landed without a hook\n\ntask:{t.id}")
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
