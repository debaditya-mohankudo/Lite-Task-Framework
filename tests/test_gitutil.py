"""taskfw.gitutil against a real repository — a mocked subprocess cannot
tell whether `rev-parse HEAD` really behaves as head_sha assumes."""
from __future__ import annotations

import shutil
import subprocess

import pytest

from taskfw import gitutil

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")


def _git(cwd, *argv):
    subprocess.run(["git", *argv], cwd=cwd, check=True, capture_output=True)


class TestHeadSha:
    def test_a_commit_gives_its_full_sha(self, tmp_path):
        _git(tmp_path, "init", "-q")
        (tmp_path / "f").write_text("x")
        _git(tmp_path, "add", "f")
        _git(tmp_path, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "c")
        want = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp_path,
                              capture_output=True, text=True).stdout.strip()
        assert gitutil.head_sha(str(tmp_path)) == want
        assert len(want) == 40

    def test_an_unborn_branch_is_unknown_not_empty(self, tmp_path):
        _git(tmp_path, "init", "-q")
        assert gitutil.head_sha(str(tmp_path)) is None

    def test_outside_a_checkout_is_unknown(self, tmp_path):
        assert gitutil.head_sha(str(tmp_path)) is None

    def test_git_missing_is_unknown_and_does_not_raise(self, tmp_path, monkeypatch):
        monkeypatch.setattr(gitutil, "run_git", lambda *a, **k: None)
        assert gitutil.head_sha(str(tmp_path)) is None
