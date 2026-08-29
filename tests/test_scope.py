"""Scope tests — which project a record belongs to, and what that filter may not do.

Three things are asserted here that would each fail silently in production.

The FIRST is normalisation. Two spellings of one remote must produce one
string; if they ever diverge, nothing breaks and nothing raises — the store
simply grows a sixth convention alongside the five already sitting in
task_commits.repo, and every scoped read quietly returns half its rows.

The SECOND is that unscoped records keep working. Every task written before
Task.scope existed carries ''. If the filter ever treats that as "belongs to no
project" rather than "project unknown", the entire pre-existing corpus vanishes
from related/lessons at once — a total, retroactive loss that no error reports.

The THIRD is that the filter narrows honestly. A scoped count is smaller than
the truth, and a count that does not say so reads as complete.
"""
from __future__ import annotations

import subprocess

import pytest

from taskfw import scope as scope_mod
from taskfw.accuracy import loop_debt
from taskfw.context import _lessons_for, _related_candidates, _same_project
from taskfw.memory import MemoryStore
from taskfw.store import TaskStore
from taskfw.task import Task


@pytest.fixture(autouse=True)
def _clear_cache():
    """derive() memoises per directory, and tmp_path differs per test — but a
    repo built mid-test would otherwise inherit the pre-init answer."""
    scope_mod.reset_cache()
    yield
    scope_mod.reset_cache()


@pytest.fixture
def store(tmp_path):
    s = TaskStore(tmp_path / "t.db")
    yield s
    s.close()


def git_repo(path, origin: str):
    path.mkdir(parents=True, exist_ok=True)
    run = lambda *a: subprocess.run(a, cwd=path, capture_output=True, check=True)  # noqa: E731
    run("git", "init", "-q")
    run("git", "remote", "add", "origin", origin)
    return path


class TestNormaliseRemote:
    """The rule that decides whether two clones agree. Pure, so tested directly."""

    def test_ssh_and_https_agree(self):
        ssh = scope_mod.normalise_remote("git@github.com:Org/Repo.git")
        https = scope_mod.normalise_remote("https://github.com/Org/Repo.git")
        assert ssh == https == "git:github.com/org/repo"

    def test_ssh_url_form_agrees_too(self):
        assert scope_mod.normalise_remote("ssh://git@github.com/Org/Repo") == "git:github.com/org/repo"

    def test_port_and_trailing_slash_do_not_split_a_repo(self):
        assert scope_mod.normalise_remote("https://git.example.com:8443/a/b/") == "git:git.example.com/a/b"

    def test_non_remotes_are_none_not_guesses(self):
        for bad in ("", "   ", "not a url", "/plain/path"):
            assert scope_mod.normalise_remote(bad) is None


class TestDerive:
    def test_a_repo_derives_from_its_origin(self, tmp_path):
        repo = git_repo(tmp_path / "r", "git@github.com:Org/Repo.git")
        assert scope_mod.derive(str(repo)) == "git:github.com/org/repo"

    def test_two_clones_of_one_repo_share_a_scope(self, tmp_path):
        """The case a path-based scope gets wrong, and the reason for origin."""
        a = git_repo(tmp_path / "a", "git@github.com:Org/Repo.git")
        b = git_repo(tmp_path / "b", "https://github.com/Org/Repo.git")
        assert scope_mod.derive(str(a)) == scope_mod.derive(str(b))

    def test_a_non_repo_falls_back_distinguishably(self, tmp_path):
        derived = scope_mod.derive(str(tmp_path))
        assert derived.startswith(scope_mod.PATH)
        assert not derived.startswith(scope_mod.GIT)

    def test_never_raises_when_git_is_unusable(self, tmp_path, monkeypatch):
        """Fail open: a scope is a filter, and a missing filter must not block a write."""
        def boom(*a, **k):
            return None
        monkeypatch.setattr(scope_mod, "run_git", boom)
        assert scope_mod.derive(str(tmp_path)).startswith(scope_mod.PATH)


class TestForRepo:
    def test_a_real_directory_derives(self, tmp_path):
        repo = git_repo(tmp_path / "r", "git@github.com:Org/Repo.git")
        assert scope_mod.for_repo(str(repo)) == "git:github.com/org/repo"

    def test_an_unresolvable_hint_is_kept_verbatim_and_marked(self):
        """Never silently relabelled to the server's cwd — unverified, not wrong."""
        assert scope_mod.for_repo("task-framework") == "hint:task-framework"

    def test_a_hint_can_never_equal_a_derived_scope(self, tmp_path):
        repo = git_repo(tmp_path / "r", "git@github.com:Org/Repo.git")
        assert scope_mod.for_repo("github.com/org/repo") != scope_mod.for_repo(str(repo))


class TestLocalRoot:
    """Turning a stored scope back into a directory `files` can be joined to,
    without ever pointing at the wrong one."""

    def test_a_path_scope_is_its_own_root(self, tmp_path):
        assert scope_mod.local_root(f"{scope_mod.PATH}{tmp_path}") == str(tmp_path)

    def test_a_path_scope_for_a_vanished_dir_is_none(self):
        assert scope_mod.local_root(f"{scope_mod.PATH}/no/such/dir/here") is None

    def test_unscoped_has_no_root(self):
        assert scope_mod.local_root("") is None

    def test_a_hint_scope_has_no_root(self):
        assert scope_mod.local_root("hint:task-framework") is None

    def test_a_git_scope_resolves_only_when_it_is_this_workspace(self, tmp_path, monkeypatch):
        repo = git_repo(tmp_path / "r", "git@github.com:Org/Repo.git")
        monkeypatch.chdir(repo)
        scope_mod.reset_cache()
        here = scope_mod.derive()
        assert here == "git:github.com/org/repo"
        assert scope_mod.local_root(here) == str(repo.resolve())

    def test_a_foreign_git_scope_never_borrows_this_repos_root(self, tmp_path, monkeypatch):
        repo = git_repo(tmp_path / "r", "git@github.com:Org/Repo.git")
        monkeypatch.chdir(repo)
        scope_mod.reset_cache()
        assert scope_mod.local_root("git:github.com/someone/else") is None


class TestTaskField:
    def test_scope_round_trips(self, store):
        store.save(Task(title="t", scope="git:github.com/org/repo"))
        assert store.list(status=None)[0].scope == "git:github.com/org/repo"

    def test_a_blob_written_before_the_field_loads_unscoped(self):
        """Not an error, and never invented — old rows read as 'project unknown'."""
        assert Task.from_dict({"id": "abc", "title": "old"}).scope == ""

    def test_scope_is_not_indexed_as_vocabulary(self):
        """A filter must narrow results in the WHERE clause, never widen the MATCH."""
        task = Task(title="t", scope="git:github.com/org/repo")
        assert "github.com" not in task.search_text()

    def test_list_filters_by_scope(self, store):
        store.save(Task(title="mine", scope="git:a/b"))
        store.save(Task(title="theirs", scope="git:c/d"))
        store.save(Task(title="legacy"))
        assert [t.title for t in store.list(status=None, scope="git:a/b")] == ["mine"]
        assert {t.title for t in store.list(status=None, scope="")} == {"legacy"}


class TestCommitRepoNormalisation:
    def test_the_fragmented_spellings_collapse(self, store, tmp_path):
        """The defect this task exists to stop: one project, many spellings."""
        repo = git_repo(tmp_path / "r", "git@github.com:Org/Repo.git")
        store.save(Task(id="t1", title="t"))
        store.add_commit("t1", "sha1", str(repo))
        store.add_commit("t1", "sha2", str(repo) + "/")
        assert {c["repo"] for c in store.commits("t1")} == {"git:github.com/org/repo"}


class TestRelatedIsScoped:
    def test_a_neighbour_from_another_project_is_excluded(self, store):
        """Word overlap cannot do this job: both share 'review' honestly."""
        mine = store.save(Task(title="ontology review", tags=["review"], scope="git:a/b"))
        store.save(Task(title="ontology review", tags=["review"], scope="git:c/d"))
        assert _related_candidates(store, mine) == []

    def test_a_neighbour_in_the_same_project_survives(self, store):
        mine = store.save(Task(title="ontology review", tags=["review"], scope="git:a/b"))
        store.save(Task(title="ontology review two", tags=["review"], scope="git:a/b"))
        assert [c["title"] for c in _related_candidates(store, mine)] == ["ontology review two"]

    def test_an_unscoped_neighbour_is_never_dropped(self, store):
        """The whole pre-existing corpus is unscoped; excluding it would erase it."""
        mine = store.save(Task(title="ontology review", tags=["review"], scope="git:a/b"))
        store.save(Task(title="ontology review two", tags=["review"]))
        assert [c["title"] for c in _related_candidates(store, mine)] == ["ontology review two"]

    def test_unscoped_is_compatible_in_both_directions(self):
        assert _same_project("", "git:a/b")
        assert _same_project("git:a/b", "")
        assert _same_project("git:a/b", "git:a/b")
        assert not _same_project("git:a/b", "git:c/d")


class TestLessonsAreMarkedNotFiltered:
    """Where related and lessons deliberately diverge: loop memory is meant to
    generalise across projects, so a foreign lesson is labelled, not hidden."""

    def _memory(self, store, slug, text, task_scope):
        """A memory learned from a task in `task_scope`. record() cites the
        task itself, so the learned_from link needs no separate call."""
        owner = store.save(Task(title="owner", scope=task_scope))
        mem = MemoryStore(conn=store.conn)
        mem.record(slug=slug, text=text, task_id=owner.id, kind="constraint")
        return mem

    def test_a_foreign_lesson_is_still_returned_but_marked(self, store):
        mem = self._memory(store, "ontology-drift", "ontology claims drift from code", "git:c/d")
        task = store.save(Task(title="ontology drift check", tags=["ontology"], scope="git:a/b"))
        lessons = _lessons_for(store, task, mem)
        assert [lesson["slug"] for lesson in lessons] == ["ontology-drift"]
        assert lessons[0]["cross_project"] is True

    def test_a_same_project_lesson_carries_no_mark(self, store):
        mem = self._memory(store, "ontology-drift", "ontology claims drift from code", "git:a/b")
        task = store.save(Task(title="ontology drift check", tags=["ontology"], scope="git:a/b"))
        lessons = _lessons_for(store, task, mem)
        assert lessons and "cross_project" not in lessons[0]


class TestLoopDebtIsHonestAboutWhatItCounted:
    def _done_with_ungraded_risk(self, store, title, scope):
        task = store.save(Task(title=title, scope=scope,
                               grooming={"risks": [{"id": "r1", "text": "x"}]}))
        task.status = "done"
        return store.save(task)

    def test_an_unscoped_call_says_it_was_global(self, store):
        self._done_with_ungraded_risk(store, "a", "git:a/b")
        assert loop_debt(store)["scope"] == "global"

    def test_a_scoped_call_counts_only_that_project(self, store):
        self._done_with_ungraded_risk(store, "mine", "git:a/b")
        self._done_with_ungraded_risk(store, "theirs", "git:c/d")
        debt = loop_debt(store, scope="git:a/b")
        assert debt["scope"] == "git:a/b"
        assert debt["tasks_examined"] == 1
        assert debt["ungraded_risks"] == 1

    def test_narrowing_reports_what_it_left_out(self, store):
        """An omission must never be indistinguishable from an absence."""
        self._done_with_ungraded_risk(store, "mine", "git:a/b")
        self._done_with_ungraded_risk(store, "legacy", "")
        debt = loop_debt(store, scope="git:a/b")
        assert debt["ungraded_risks"] == 1
        assert debt["unscoped_not_counted"] == {
            "skipped_introspection": 1, "ungraded_risks": 1,
        }

    def test_nothing_is_claimed_when_there_is_no_remainder(self, store):
        self._done_with_ungraded_risk(store, "mine", "git:a/b")
        assert "unscoped_not_counted" not in loop_debt(store, scope="git:a/b")
