"""concept__store_status: /commit's warning that concept edits are not in git.

Run against real temporary git repositories, since what matters is how
`git status` actually reports each state (task:ce6d7830). The origin is
cli-graph, whose concept_store/ was never added to git while three tasks
ticked concept edits as done.
"""
from __future__ import annotations

import shutil
import subprocess

import pytest

from taskfw import gitutil
from taskfw import mcp_server as m
from taskfw.concepts import STORE_RELPATH

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")

STORE_FILE = STORE_RELPATH
STORE_DIR = STORE_RELPATH.parent
CONCEPT = {"name": "a-concept", "module": "code.py", "description": "A concept."}


def _git(cwd, *argv):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *argv],
                   cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    """A checkout with one committed code file and no concept store yet."""
    _git(tmp_path, "init", "-q")
    (tmp_path / "code.py").write_text("x = 1\n")
    _git(tmp_path, "add", "code.py")
    _git(tmp_path, "commit", "-qm", "code")
    return tmp_path


def _write_store(repo):
    (repo / STORE_DIR).mkdir(exist_ok=True)
    (repo / STORE_FILE).write_text('{"concepts": {}}\n')


def _commit_store(repo):
    _write_store(repo)
    _git(repo, "add", str(STORE_FILE))
    _git(repo, "commit", "-qm", "store")


def _status(repo):
    return m.concept__store_status(str(repo))


class TestWarns:
    def test_the_cli_graph_case_an_untracked_store_beside_committed_code(self, repo):
        _write_store(repo)
        out = _status(repo)
        assert out["state"] == "untracked"
        assert str(repo / STORE_DIR) in out["warning"]

    def test_an_unstaged_edit_to_a_tracked_store(self, repo):
        _commit_store(repo)
        (repo / STORE_FILE).write_text('{"concepts": {"x": {}}}\n')
        out = _status(repo)
        assert out["state"] == "modified"
        assert str(repo / STORE_DIR) in out["warning"]

    def test_a_staged_but_uncommitted_edit(self, repo):
        _commit_store(repo)
        (repo / STORE_FILE).write_text('{"concepts": {"x": {}}}\n')
        _git(repo, "add", str(STORE_FILE))
        assert _status(repo)["state"] == "modified"

    def test_a_new_file_beside_a_modified_tracked_one_is_modified(self, repo):
        _commit_store(repo)
        (repo / STORE_FILE).write_text('{"concepts": {"x": {}}}\n')
        (repo / STORE_DIR / "README.md").write_text("new\n")
        assert _status(repo)["state"] == "modified"

    def test_an_upsert_is_seen_at_the_path_upsert_wrote(self, repo):
        """Same repo resolution as concept__upsert, so the two cannot disagree."""
        assert m.concept__upsert(str(repo), dict(CONCEPT))["ok"]
        out = _status(repo)
        assert out["state"] == "untracked"
        assert out["path"] == str(repo / STORE_DIR)


class TestSilent:
    def test_a_committed_clean_store(self, repo):
        _commit_store(repo)
        assert _status(repo) == {"path": str(repo / STORE_DIR), "state": "clean"}

    def test_a_repo_with_no_store(self, repo):
        assert _status(repo) == {"path": str(repo / STORE_DIR), "state": "absent"}

    def test_a_gitignored_store_is_ignored_not_clean(self, repo):
        (repo / ".gitignore").write_text("concept_store/\n")
        _write_store(repo)
        out = _status(repo)
        assert out["state"] == "ignored"
        assert "warning" not in out

    def test_outside_a_checkout_is_no_git_not_clean(self, tmp_path):
        _write_store(tmp_path)
        out = _status(tmp_path)
        assert out["state"] == "no_git"
        assert "warning" not in out

    def test_a_malformed_store_still_reports_its_git_state(self, repo):
        """The check reads git, never the JSON, so a broken store cannot stop it."""
        (repo / STORE_DIR).mkdir()
        (repo / STORE_FILE).write_text("{not json")
        assert _status(repo)["state"] == "untracked"

    def test_git_missing_is_no_git_and_does_not_raise(self, repo, monkeypatch):
        _write_store(repo)
        monkeypatch.setattr(gitutil, "run_git", lambda *a, **k: None)
        assert _status(repo)["state"] == "no_git"
