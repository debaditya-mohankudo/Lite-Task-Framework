"""MCP tool tests — the portable surface.

Tools are exercised by calling them directly rather than over the wire —
MCPServer's decorator registers the function and returns it unchanged, so this
reaches the same code path a host does without standing up a transport.

The property worth protecting here is that tools enforce nothing themselves.
Every rule check goes through taskfw.lifecycle, so a tool cannot enforce a
different set than a hook does.
"""
from __future__ import annotations

import json

import pytest

from taskfw import mcp_server as m
from taskfw.store import TaskStore


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    s = TaskStore(tmp_path / "t.db")
    m.set_store(s)
    monkeypatch.setenv("TASKFW_SCOPE", "/test/workspace")
    # Point the claude-hooks push at a port nothing listens on, so tests never
    # depend on (or pollute) a real claude-hooks server that happens to be
    # running on this machine. connection-refused is fast and swallowed by
    # _push_active_task exactly like any other unreachable-server case.
    monkeypatch.setenv("CLAUDE_HOOKS_URL", "http://127.0.0.1:1")
    yield s
    s.close()
    m.set_store(None)


def create(**kw):
    kw.setdefault("title", "A task")
    return m.tasks__create(**kw)


class TestCreate:
    def test_creates_a_task(self):
        r = create(title="Build it")
        assert r["ok"] and r["epic"] is False and r["status"] == "open"

    def test_creates_an_epic(self):
        assert create(title="An epic", epic=True)["epic"] is True

    def test_rejects_an_epic_with_a_parent(self):
        e = create(title="e", epic=True)
        r = create(title="e2", epic=True, parent=e["id"])
        assert r["rule"] == "parent"

    def test_rejects_a_missing_parent(self):
        assert "error" in create(parent="nosuch")

    def test_resolution_is_a_plain_list_of_strings(self):
        r = create(resolution=["one", "two"])
        assert m.tasks__get(r["id"])["resolution"] == [
            {"text": "one", "done": False}, {"text": "two", "done": False}
        ]

    def test_surfaces_related_candidates_when_titles_overlap(self):
        create(title="Add hit_count tracking")
        r = create(title="Add usage tracking to memory")
        assert "related_candidates" in r
        assert any("hit_count" in c["title"] for c in r["related_candidates"])

    def test_omits_related_candidates_when_none_found(self):
        r = create(title="Completely unrelated one-off task xyzzy")
        assert "related_candidates" not in r

    def test_new_task_never_appears_in_its_own_candidates(self):
        create(title="Wire up the widget")
        r = create(title="Wire up the widget again")
        assert r["id"] not in [c["id"] for c in r.get("related_candidates", [])]


class TestPhase:
    def test_all_false_on_a_fresh_task(self):
        r = create(resolution=["one"])
        phase = m.tasks__phase(r["id"])
        assert phase["id"] == r["id"]
        assert phase["groomed"] is False and phase["implemented"] is False and phase["introspected"] is False

    def test_implemented_true_once_checklist_is_complete(self):
        r = create(resolution=["one"])
        m.tasks__check_item(r["id"], index=0, done=True)
        assert m.tasks__phase(r["id"])["implemented"] is True

    def test_introspected_true_once_a_report_exists(self):
        r = create()
        m.tasks__add_introspection(r["id"], report={"date": "2026-08-04"})
        assert m.tasks__phase(r["id"])["introspected"] is True

    def test_unknown_task_is_an_error(self):
        assert "error" in m.tasks__phase("nosuch")

    def test_never_persists_anything_new(self):
        """Phase is computed on every call, never cached or stored — two calls
        against an unchanged task must agree without any write in between."""
        r = create(resolution=["one"])
        first = m.tasks__phase(r["id"])
        second = m.tasks__phase(r["id"])
        assert first == second


class TestUpdate:
    def test_only_given_fields_change(self):
        t = create(title="original", motivation="keep me")
        m.tasks__update(t["id"], title="renamed")
        got = m.tasks__get(t["id"])
        assert got["title"] == "renamed" and got["motivation"] == "keep me"

    def test_illegal_transition_is_refused_by_lifecycle(self):
        t = create()
        m.tasks__update(t["id"], status="done")
        r = m.tasks__update(t["id"], status="open")
        assert r["rule"] == "transition"

    def test_legal_transition_is_allowed(self):
        t = create()
        assert m.tasks__update(t["id"], status="blocked")["status"] == "blocked"

    def test_unknown_task(self):
        assert "error" in m.tasks__update("nope", title="x")

    def test_finish_reminder_fires_when_resolution_replaced_all_done(self, store):
        # task:f302eb2b: tasks__check_item now auto-finishes a task the
        # moment its last item is checked, so an "all done but still open"
        # task can no longer be reached through check_item — only by editing
        # the store directly (e.g. data from before auto-finish existed).
        # finish_reminder_nudge still needs to catch that state via update.
        t = create()
        r = m.tasks__update(t["id"], resolution=["a"])
        assert "finish_reminder_nudge" not in r  # replacement items default to not-done
        task = store.get(t["id"])
        task.resolution[0].done = True
        store.save(task)
        r = m.tasks__update(t["id"], title="still working on it")
        assert "finish_reminder_nudge" in r

    def test_ungroomed_progress_fires_once_a_checked_item_exists(self):
        t = create(resolution=["a"])
        m.tasks__check_item(t["id"], 0)
        r = m.tasks__update(t["id"], title="still working on it")
        assert "ungroomed_progress_nudge" in r and t["id"] in r["ungroomed_progress_nudge"]

    def test_no_ungroomed_progress_once_grooming_is_recorded(self):
        t = create(resolution=["a"])
        m.tasks__check_item(t["id"], 0)
        m.tasks__update(t["id"], grooming={"clarifications": ["x"]})
        r = m.tasks__update(t["id"], title="still working on it")
        assert "ungroomed_progress_nudge" not in r


class TestGroomingRiskMerge:
    """task:f24be6e4 — a risk gets a stable id, and re-grooming can't drop a grade."""

    def test_a_new_risk_gets_a_framework_assigned_id(self):
        t = create()
        r = m.tasks__update(t["id"], grooming={"risks": [{"text": "might break", "graded": None}]})
        risks = m.tasks__get(t["id"])["grooming"]["risks"]
        assert len(risks) == 1 and risks[0]["id"]
        assert r  # update succeeded

    def test_omitting_a_graded_risk_on_re_groom_carries_it_forward(self):
        t = create()
        m.tasks__update(t["id"], grooming={"risks": [{"text": "a", "graded": "materialized"}]})
        rid = m.tasks__get(t["id"])["grooming"]["risks"][0]["id"]
        # Re-groom omits the risk entirely, as a real re-groom pass would if
        # it only wrote new clarifications and forgot to re-paste risks.
        m.tasks__update(t["id"], grooming={"clarifications": ["new fact"]})
        risks = m.tasks__get(t["id"])["grooming"]["risks"]
        assert len(risks) == 1
        assert risks[0]["id"] == rid
        assert risks[0]["graded"] == "materialized"

    def test_omitting_an_ungraded_risk_on_re_groom_drops_it(self):
        t = create()
        m.tasks__update(t["id"], grooming={"risks": [{"text": "a", "graded": None}]})
        m.tasks__update(t["id"], grooming={"risks": []})
        assert m.tasks__get(t["id"])["grooming"]["risks"] == []

    def test_rewording_a_risk_by_id_preserves_its_grade(self):
        t = create()
        m.tasks__update(t["id"], grooming={"risks": [{"text": "a", "graded": "avoided"}]})
        rid = m.tasks__get(t["id"])["grooming"]["risks"][0]["id"]
        m.tasks__update(t["id"], grooming={
            "risks": [{"id": rid, "text": "a, reworded", "graded": "avoided"}]
        })
        risks = m.tasks__get(t["id"])["grooming"]["risks"]
        assert len(risks) == 1
        assert risks[0]["id"] == rid
        assert risks[0]["text"] == "a, reworded"
        assert risks[0]["graded"] == "avoided"

    def test_omitting_graded_on_an_id_matched_reword_keeps_the_existing_grade(self):
        """A caller rewording a risk's text but not re-sending `graded` (an
        ordinary partial edit, not an intentional re-grade) must not reset the
        grade to None just because the field was left out of the payload —
        the same protection re-groom omission already gets for the whole risk."""
        t = create()
        m.tasks__update(t["id"], grooming={"risks": [{"text": "a", "graded": "avoided"}]})
        rid = m.tasks__get(t["id"])["grooming"]["risks"][0]["id"]
        m.tasks__update(t["id"], grooming={
            "risks": [{"id": rid, "text": "a, reworded"}]
        })
        risks = m.tasks__get(t["id"])["grooming"]["risks"]
        assert len(risks) == 1
        assert risks[0]["text"] == "a, reworded"
        assert risks[0]["graded"] == "avoided"

    def test_legacy_id_less_risk_matched_by_text_gains_an_id_without_duplicating(self):
        t = create()
        store_obj = m.store()
        task = store_obj.get(t["id"])
        task.grooming = {"risks": [{"text": "legacy risk", "graded": "wrong"}]}
        store_obj.save(task)
        # Re-groom re-pastes the same text with no id, as the old workflow did.
        m.tasks__update(t["id"], grooming={"risks": [{"text": "legacy risk", "graded": "wrong"}]})
        risks = m.tasks__get(t["id"])["grooming"]["risks"]
        assert len(risks) == 1
        assert risks[0]["id"]

    def test_other_grooming_fields_stay_wholesale_replace(self):
        t = create()
        m.tasks__update(t["id"], grooming={"clarifications": ["old"]})
        m.tasks__update(t["id"], grooming={"clarifications": ["new"]})
        assert m.tasks__get(t["id"])["grooming"]["clarifications"] == ["new"]


class TestChecklist:
    def test_ticking_updates_progress(self):
        t = create(resolution=["a", "b"])
        r = m.tasks__check_item(t["id"], 0)
        assert r["progress"] == {"done": 1, "total": 2}

    def test_out_of_range_index_is_rejected(self):
        t = create(resolution=["a"])
        assert "error" in m.tasks__check_item(t["id"], 5)

    def test_completing_the_last_item_auto_finishes_instead_of_reminding(self):
        """task:f302eb2b: checking off the last item now finishes the task
        outright, so finish_reminder_nudge (which only fires while still
        open) never gets a chance to — there is nothing left to remind about."""
        t = create(resolution=["a", "b"])
        m.tasks__check_item(t["id"], 0)
        r = m.tasks__check_item(t["id"], 1)
        assert "finish_reminder_nudge" not in r
        assert r["status"] == "done"

    def test_no_finish_reminder_on_partial_completion(self):
        t = create(resolution=["a", "b"])
        r = m.tasks__check_item(t["id"], 0)
        assert "finish_reminder_nudge" not in r

    def test_no_finish_reminder_once_the_task_is_done(self):
        t = create(resolution=["a"])
        m.tasks__check_item(t["id"], 0)
        m.tasks__finish(t["id"])
        r = m.tasks__check_item(t["id"], 0)
        assert "finish_reminder_nudge" not in r

    def test_ungroomed_progress_fires_on_checking_an_item(self):
        t = create(resolution=["a", "b"])
        r = m.tasks__check_item(t["id"], 0)
        assert "ungroomed_progress_nudge" in r and t["id"] in r["ungroomed_progress_nudge"]

    def test_no_ungroomed_progress_when_task_was_groomed(self):
        t = create(resolution=["a", "b"])
        m.tasks__update(t["id"], grooming={"clarifications": ["x"]})
        r = m.tasks__check_item(t["id"], 0)
        assert "ungroomed_progress_nudge" not in r

    def test_checking_first_item_activates_the_task_when_nothing_else_is_active(self):
        t = create(resolution=["a", "b"])
        assert m.tasks__active()["active"] is None
        m.tasks__check_item(t["id"], 0)
        assert m.tasks__active()["active"] == t["id"]

    def test_checking_the_only_item_finishes_without_ever_activating(self):
        """task:f302eb2b: a single-item checklist skips activation entirely
        and goes straight to auto-finish — there's no intermediate 'in
        progress' state to push onto the stack for."""
        t = create(resolution=["a"])
        assert m.tasks__active()["active"] is None
        r = m.tasks__check_item(t["id"], 0)
        assert r["status"] == "done"
        assert m.tasks__active()["active"] is None

    def test_checking_an_item_on_the_already_active_task_is_a_silent_no_op(self):
        t = create(resolution=["a", "b"])
        m.tasks__set_active(t["id"])
        r = m.tasks__check_item(t["id"], 1)
        assert "active_task_notice" not in r
        assert m.tasks__active()["active"] == t["id"]

    def test_checking_an_item_on_a_different_task_replaces_the_active_task(self):
        """task:f5ace343: checking off an item on a non-active task simply
        replaces the active task rather than being refused."""
        a = create(resolution=["a"])
        b = create(resolution=["b", "c"])
        m.tasks__set_active(a["id"])
        m.tasks__check_item(b["id"], 0)
        assert m.tasks__active()["active"] == b["id"]

    def test_unchecking_an_item_does_not_activate_the_task(self):
        t = create(resolution=["a"])
        m.tasks__check_item(t["id"], 0, done=False)
        assert m.tasks__active()["active"] is None


class TestFinish:
    def test_finishes_an_open_task(self):
        t = create()
        assert m.tasks__finish(t["id"], reason="shipped")["status"] == "done"

    def test_finishing_an_already_done_task_is_idempotent(self):
        """Follows from the same-status rule, and is the friendlier behaviour.

        A retry or a duplicate call should not surface as an error when the
        task is already in the state the caller wants.
        """
        t = create()
        m.tasks__finish(t["id"])
        r = m.tasks__finish(t["id"])
        assert r["status"] == "done" and "error" not in r

    def test_refuses_to_finish_an_abandoned_task(self):
        """Abandoned is terminal and is NOT the state the caller asked for."""
        t = create()
        m.tasks__update(t["id"], status="abandoned")
        assert m.tasks__finish(t["id"])["rule"] == "transition"

    def test_nudges_toward_introspection_when_none_was_recorded(self):
        t = create()
        r = m.tasks__finish(t["id"])
        assert "introspection_nudge" in r

    def test_no_nudge_once_a_report_exists(self):
        t = create()
        m.tasks__add_introspection(t["id"], {"new_knowledge": ["a lesson"]})
        r = m.tasks__finish(t["id"])
        assert "introspection_nudge" not in r

    def test_no_nudge_on_a_refused_finish(self):
        t = create()
        m.tasks__update(t["id"], status="abandoned")
        assert "introspection_nudge" not in m.tasks__finish(t["id"])


class TestListAndSearch:
    def test_list_defaults_to_open_and_blocked(self):
        a = create(title="open one")
        b = create(title="done one")
        m.tasks__finish(b["id"])
        assert [t["title"] for t in m.tasks__list()] == ["open one"]
        assert len(m.tasks__list(status="")) == 2

    def test_search_finds_by_motivation(self):
        create(title="alpha", motivation="uses a widget")
        create(title="beta")
        assert [t["title"] for t in m.tasks__search("widget")] == ["alpha"]

    def test_list_exposes_timestamps_newest_first(self, monkeypatch):
        import taskfw.store as store_mod

        monkeypatch.setattr(store_mod, "utcnow", lambda: "2020-01-01 00:00:00")
        create(title="older")
        monkeypatch.setattr(store_mod, "utcnow", lambda: "2030-01-01 00:00:00")
        create(title="newer")
        rows = m.tasks__list(status="")
        assert all("created_at" in r and "updated_at" in r for r in rows)
        assert [r["title"] for r in rows] == ["newer", "older"]


class TestGraph:
    def test_link_unlink_round_trip(self):
        a, b = create(title="a"), create(title="b")
        assert m.tasks__link(a["id"], b["id"], "depends_on")["created"] is True
        assert m.tasks__edges(a["id"])["outgoing"][0]["to_id"] == b["id"]
        assert m.tasks__unlink(a["id"], b["id"])["removed"] == 1

    def test_link_rejects_unknown_tasks(self):
        a = create()
        assert "error" in m.tasks__link(a["id"], "nosuch")

    def test_link_rejects_an_unrecognised_relation(self):
        a, b = create(title="a"), create(title="b")
        r = m.tasks__link(a["id"], b["id"], "made_up_rel")
        assert r["rule"] == "rel" and "error" in r

    def test_unlink_is_never_gated_by_the_closed_set(self, store):
        """A legacy/out-of-vocabulary edge (bypassing tasks__link's gate,
        simulating a pre-existing row) must still be removable via the tool."""
        a, b = create(title="a"), create(title="b")
        store.link(a["id"], b["id"], "parent_of")
        assert m.tasks__unlink(a["id"], b["id"], "parent_of")["removed"] == 1

    def test_commit_capture_is_idempotent(self):
        t = create()
        assert m.tasks__add_commit(t["id"], "abc")["recorded"] is True
        assert m.tasks__add_commit(t["id"], "abc")["recorded"] is False


class TestFormatCommitMessage:
    def test_unknown_task_is_an_error(self):
        assert "error" in m.tasks__format_commit_message("nosuch", "A subject")

    def test_empty_subject_is_an_error(self):
        t = create()
        assert "error" in m.tasks__format_commit_message(t["id"], "   ")

    def test_multiline_subject_is_an_error(self):
        t = create()
        assert "error" in m.tasks__format_commit_message(t["id"], "line one\nline two")

    def test_trailing_period_is_stripped(self):
        t = create()
        r = m.tasks__format_commit_message(t["id"], "A subject.")
        assert r["message"].startswith("A subject\n\n")

    def test_shape_without_body(self):
        t = create()
        r = m.tasks__format_commit_message(t["id"], "A subject")
        assert r["message"] == f"A subject\n\ntask:{t['id']}"

    def test_shape_with_body(self):
        t = create()
        r = m.tasks__format_commit_message(t["id"], "A subject", body="Why this exists.")
        assert r["message"] == f"A subject\n\ntask:{t['id']}\n\nWhy this exists."


class TestActiveTask:
    def test_creating_a_task_clears_the_active_one(self):
        """task:74bf3542: a fresh task is a clean break — it does not inherit
        whatever was active, and the old active task is not left dangling."""
        t = create()
        m.tasks__set_active(t["id"])
        create(title="a new task")
        assert m.tasks__active()["active"] is None

    def test_creating_a_task_with_none_active_is_a_no_op(self):
        assert m.tasks__active()["active"] is None
        create()
        assert m.tasks__active()["active"] is None

    def test_set_get_clear(self):
        t = create()
        assert m.tasks__active()["active"] is None
        m.tasks__set_active(t["id"])
        assert m.tasks__active()["active"] == t["id"]
        m.tasks__clear_active()
        assert m.tasks__active()["active"] is None

    def test_set_active_rejects_unknown_task(self):
        assert "error" in m.tasks__set_active("nosuch")

    def test_set_active_replaces_rather_than_refuses_a_switch(self):
        """task:f5ace343: switching used to require confirm=True; now active
        status is a single ephemeral pointer, so switching just replaces it,
        non-destructively, with nothing to confirm."""
        a, b = create(title="a"), create(title="b")
        m.tasks__set_active(a["id"])
        result = m.tasks__set_active(b["id"])
        assert result["ok"]
        assert m.tasks__active()["active"] == b["id"]

    def test_set_active_already_active_is_a_no_op(self):
        t = create()
        assert m.tasks__set_active(t["id"])["ok"]
        assert m.tasks__set_active(t["id"])["ok"]
        assert m.tasks__active()["active"] == t["id"]

    def test_finish_clears_the_active_task_it_finished(self):
        t = create()
        m.tasks__set_active(t["id"])
        m.tasks__finish(t["id"])
        assert m.tasks__active()["active"] is None

    def test_finish_leaves_a_different_active_task_untouched(self):
        t = create()
        other = create(title="other")
        m.tasks__set_active(t["id"])
        m.tasks__finish(other["id"])
        assert m.tasks__active()["active"] == t["id"]

    def test_update_to_done_clears_the_active_task(self):
        t = create()
        m.tasks__set_active(t["id"])
        m.tasks__update(t["id"], status="done")
        assert m.tasks__active()["active"] is None

    def test_update_to_abandoned_pops_the_active_task(self):
        t = create()
        m.tasks__set_active(t["id"])
        m.tasks__update(t["id"], status="abandoned")
        assert m.tasks__active()["active"] is None

    def test_update_to_done_leaves_a_different_active_task_untouched(self):
        t = create()
        other = create(title="other")
        m.tasks__set_active(t["id"])
        m.tasks__update(other["id"], status="done")
        assert m.tasks__active()["active"] == t["id"]

    def test_update_with_non_status_fields_does_not_touch_the_active_stack(self):
        t = create()
        m.tasks__set_active(t["id"])
        m.tasks__update(t["id"], title="renamed while active")
        assert m.tasks__active()["active"] == t["id"]

    def test_clear_active_clears_it(self):
        t = create()
        m.tasks__set_active(t["id"])
        result = m.tasks__clear_active()
        assert result["active"] is None
        assert m.tasks__active()["active"] is None

    def test_context_falls_back_to_the_active_task(self):
        t = create(title="the active one")
        m.tasks__set_active(t["id"])
        assert m.tasks__context()["task"]["title"] == "the active one"

    def test_context_without_task_or_active_explains_itself(self):
        assert "error" in m.tasks__context()


class TestLoopDebtNudge:
    """task:07f9270c — skipped introspection surfaced at tasks__set_active."""

    def test_clean_loop_is_silent(self):
        t = create()
        r = m.tasks__set_active(t["id"])
        assert "loop_debt_nudge" not in r and "task_debt_nudge" not in r

    def test_a_finished_task_with_ungraded_risks_surfaces_as_loop_debt(self):
        skipped = create(title="skipped")
        m.tasks__update(skipped["id"], grooming={"risks": [{"text": "a", "graded": None}]})
        m.tasks__update(skipped["id"], status="done")
        t = create(title="unrelated")
        r = m.tasks__set_active(t["id"])
        assert "1 of the last" in r["loop_debt_nudge"]

    def test_the_active_task_s_own_ungraded_risks_surface_as_task_debt(self):
        t = create()
        m.tasks__update(t["id"], grooming={"risks": [{"text": "a", "graded": None}]})
        r = m.tasks__set_active(t["id"])
        assert f"task:{t['id']} has 1 risk" in r["task_debt_nudge"]
        assert "loop_debt_nudge" not in r  # t isn't finished, so it's not loop debt

    def test_a_graded_risk_produces_no_task_debt(self):
        t = create()
        m.tasks__update(t["id"], grooming={"risks": [{"text": "a", "graded": "avoided"}]})
        r = m.tasks__set_active(t["id"])
        assert "task_debt_nudge" not in r

    def test_loop_debt_count_matches_grooming_accuracy(self):
        skipped = create(title="skipped")
        m.tasks__update(skipped["id"], grooming={"risks": [{"text": "a", "graded": None}]})
        m.tasks__update(skipped["id"], status="done")
        t = create(title="unrelated")
        r = m.tasks__set_active(t["id"])
        assert skipped["id"] in m.tasks__grooming_accuracy()["skipped_introspection"]
        assert "1 of the last" in r["loop_debt_nudge"]


class TestClaudeHooksPush:
    """tasks__set_active/clear_active best-effort POST to claude-hooks'
    /set-active-taskid (task:6906557f, claude-hooks task:996cc8f0)."""

    def test_set_active_pushes_workspace_task_id_and_title(self, monkeypatch):
        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append((req.full_url, json.loads(req.data), timeout))
            return _DummyResponse()

        monkeypatch.setattr(m.urllib.request, "urlopen", fake_urlopen)
        t = create(title="Push me")
        m.tasks__set_active(t["id"])

        assert len(calls) == 1
        url, body, timeout = calls[0]
        assert url == "http://127.0.0.1:1/set-active-taskid"
        assert body == {"workspace": "/test/workspace", "task_id": t["id"], "title": "Push me"}
        assert timeout == m._PUSH_TIMEOUT_S

    def test_clear_active_pushes_empty_task_id(self, monkeypatch):
        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append(json.loads(req.data))
            return _DummyResponse()

        monkeypatch.setattr(m.urllib.request, "urlopen", fake_urlopen)
        t = create()
        m.tasks__set_active(t["id"])
        m.tasks__clear_active()

        assert calls[-1] == {"workspace": "/test/workspace", "task_id": "", "title": ""}

    def test_set_active_still_succeeds_when_push_raises(self, monkeypatch):
        def raising_urlopen(req, timeout=None):
            raise ConnectionRefusedError("no server")

        monkeypatch.setattr(m.urllib.request, "urlopen", raising_urlopen)
        t = create()
        result = m.tasks__set_active(t["id"])

        assert result["ok"]
        assert m.tasks__active()["active"] == t["id"]

    def test_set_active_unreachable_url_does_not_raise(self):
        # No monkeypatch here — exercises the real urllib path against the
        # unreachable CLAUDE_HOOKS_URL set by the store fixture, proving the
        # actual network/connection-refused exception (not a mock) is caught.
        t = create()
        result = m.tasks__set_active(t["id"])
        assert result["ok"]


class _DummyResponse:
    def close(self):
        pass


class TestDecisions:
    def test_decision_surfaces_in_context(self):
        t = create()
        m.tasks__add_decision(t["id"], "chose the library route")
        ctx = m.tasks__context(t["id"])
        assert ctx["decisions"][0]["text"] == "chose the library route"

    def test_decision_on_unknown_task(self):
        assert "error" in m.tasks__add_decision("nosuch", "x")


class TestGroomingAccuracy:
    """The one tool that reads across tasks rather than into one."""

    def test_reports_zeroes_on_an_empty_store(self):
        r = m.tasks__grooming_accuracy()
        assert r["tasks_examined"] == 0 and r["signals"] == []

    def test_aggregates_grades_from_finished_tasks(self, store):
        tid = create(title="Groomed")["id"]
        m.tasks__update(tid, grooming={"risks": [
            {"text": "a", "graded": "materialized"}, {"text": "b", "graded": "wrong"}]})
        m.tasks__finish(tid)
        r = m.tasks__grooming_accuracy()
        assert r["risks"]["materialized"] == 1 and r["risks"]["wrong"] == 1
        assert r["predictive_value"] == 0.5

    def test_matching_risk_text_across_two_independently_groomed_tasks_recurs(self, store):
        """End-to-end: each task is groomed through tasks__update the way the
        grooming skill actually does it — an id-less risk — so each gets its
        own framework-assigned id (task:f24be6e4). Recurrence must still see
        these as the same predicted risk; keying purely by id would key two
        distinct fresh ids apart and never surface the pattern."""
        one = create(title="one")["id"]
        two = create(title="two")["id"]
        m.tasks__update(one, grooming={"risks": [{"text": "migration could lock the table", "graded": "wrong"}]})
        m.tasks__update(two, grooming={"risks": [{"text": "migration could lock the table", "graded": "wrong"}]})
        m.tasks__finish(one)
        m.tasks__finish(two)
        recurring = m.tasks__grooming_accuracy()["recurring_risks"]
        assert len(recurring) == 1
        assert set(recurring[0]["tasks"]) == {one, two}

    def test_flags_a_finished_task_that_graded_nothing(self, store):
        tid = create(title="Ungraded")["id"]
        m.tasks__update(tid, grooming={"risks": [{"text": "a", "graded": None}]})
        m.tasks__finish(tid)
        assert m.tasks__grooming_accuracy()["skipped_introspection"] == [tid]


class TestSkillInvocationLogging:
    def test_writes_a_log_line_naming_the_skill_and_task(self):
        """get_logger() routes through JsonlHandler during tests (see
        test_logging.py's TestJsonlSink) — tasks__logs (SQL-only) intentionally
        never sees test-mode log calls, same as any other taskfw log line, so
        this asserts against the same session jsonl file real calls land in.
        In production, query back with tasks__logs(logger="taskfw.skill.<name>")
        — get_logger() prefixes every name with "taskfw." (see
        test_logging.py::TestSQLiteSink::test_skill_invocation_logger_name_is_queryable_with_taskfw_prefix).
        """
        import os

        r = m.tasks__log_skill_invocation("a-test-skill", task_id="abc123")
        assert r["ok"] is True

        path = os.environ["TASKFW_LOG_JSONL"]
        with open(path) as f:
            assert any("skill.a-test-skill" in line and "abc123" in line for line in f)

    def test_empty_task_id_does_not_error(self):
        r = m.tasks__log_skill_invocation("another-skill")
        assert r["ok"] is True and r["task_id"] == ""


class TestIntrospection:
    """memory_nudge — see taskfw.dispatcher.introspection_nudge for the logic itself."""

    def test_nudges_on_the_canonical_new_knowledge_shape(self):
        tid = create(title="Had a surprise")["id"]
        r = m.tasks__add_introspection(
            tid, {"new_knowledge": ["verify the coupling before designing around it"]})
        assert "memory_nudge" in r

    def test_nudges_on_the_legacy_surprises_lesson_shape(self):
        tid = create(title="Had a surprise")["id"]
        r = m.tasks__add_introspection(tid, {"surprises": [
            {"surprise": "x", "lesson": "verify the coupling before designing around it"}
        ]})
        assert "memory_nudge" in r

    def test_no_nudge_once_the_lesson_is_recorded(self):
        tid = create(title="Had a surprise")["id"]
        report = {"new_knowledge": ["verify the coupling first"]}
        m.task_memory__record(slug="verify-coupling-first", task_id=tid,
                               text="Verify an assumed coupling is real before designing a fix for it.")
        r = m.tasks__add_introspection(tid, report)
        assert "memory_nudge" not in r

    def test_no_nudge_when_there_is_nothing_to_promote(self):
        tid = create(title="Uneventful")["id"]
        r = m.tasks__add_introspection(tid, {"surprises": [{"surprise": "x"}],
                                              "missed_surprises": ["nothing generalized"]})
        assert "memory_nudge" not in r


class TestFullLifecycle:
    def test_create_groom_implement_introspect(self):
        """One pass through the whole loop, asserting the state each stage leaves behind.

        The other classes in this file pin each tool's behaviour in isolation
        (risk merge, nudges, transitions); nothing chains them the way a real
        task actually moves through the loop. This is that chain, checked at
        the end against tasks__get rather than against each call's own return
        value, so it fails if any stage silently didn't persist what the
        previous stage produced.
        """
        # create
        t = create(title="Add a widget", resolution=["build it", "test it"])
        tid = t["id"]
        assert m.tasks__get(tid)["status"] == "open"

        # groom: record a risk prediction before implementation starts
        groom = m.tasks__update(tid, grooming={
            "clarifications": ["the widget slots into the existing panel"],
            "risks": [{"text": "panel layout may not have room for it"}],
        })
        assert groom["ok"]
        risk_id = m.tasks__get(tid)["grooming"]["risks"][0]["id"]

        # implement: work happens, checklist gets ticked off
        m.tasks__check_item(tid, 0)
        r = m.tasks__check_item(tid, 1)
        assert r["status"] == "done"  # last item checked auto-finishes the task

        # introspect: grade the risk and record what was learned
        m.tasks__update(tid, grooming={
            "clarifications": ["the widget slots into the existing panel"],
            "risks": [{"id": risk_id, "text": "panel layout may not have room for it",
                       "graded": "avoided"}],
        })
        report = m.tasks__add_introspection(tid, {
            "date": "2026-01-01",
            "new_knowledge": ["panel has more slack than it looks like"],
        })
        assert report["ok"] and report["reports"] == 1

        final = m.tasks__get(tid)
        assert final["status"] == "done"
        assert all(r["done"] for r in final["resolution"])
        assert final["grooming"]["risks"][0] == {
            "id": risk_id, "text": "panel layout may not have room for it",
            "graded": "avoided",
        }
        assert final["introspection"] == [
            {"date": "2026-01-01", "new_knowledge": ["panel has more slack than it looks like"]}
        ]


class TestLoopMemory:
    """Scoped to what introspection produces; everything else has a home."""

    def test_records_a_lesson_against_a_real_task(self):
        tid = create(title="Finished")["id"]
        r = m.task_memory__record(
            slug="degrade-fts-to-like", task_id=tid,
            text="FTS5 is a compile-time option, so search must degrade rather than fail.")
        assert r["ok"] and r["memory"]["learned_from"] == [tid]
        assert r["memory"]["standing"] == "unverified"

    def test_refuses_a_memory_citing_no_real_task(self):
        r = m.task_memory__record(slug="a-slug", task_id="nope",
                                  text="A lesson long enough to count as one.")
        assert "error" in r and "nope" in r["error"]

    def test_shape_errors_come_back_as_errors_not_exceptions(self):
        tid = create(title="Finished")["id"]
        assert "error" in m.task_memory__record(slug="Not A Slug", task_id=tid,
                                                text="A lesson long enough to count.")
        assert "error" in m.task_memory__record(slug="ok-slug", task_id=tid, text="short")

    def test_standing_is_graded_by_later_tasks(self):
        tid = create(title="Source")["id"]
        later = create(title="Later")["id"]
        m.task_memory__record(slug="a-lesson", task_id=tid,
                              text="A lesson long enough to count as one.")
        r = m.task_memory__link(slug="a-lesson", task_id=later, relation="contradicted_by")
        assert r["memory"]["standing"] == "contradicted"

    def test_superseded_memory_leaves_recall(self):
        tid = create(title="Source")["id"]
        for slug in ("old-lesson", "new-lesson"):
            m.task_memory__record(slug=slug, task_id=tid,
                                  text="A lesson long enough to count as one.")
        m.task_memory__supersede(slug="old-lesson", by="new-lesson")
        assert [x["slug"] for x in m.task_memory__recall()["memories"]] == ["new-lesson"]

    def test_forget_removes_it_entirely(self):
        tid = create(title="Source")["id"]
        m.task_memory__record(slug="wrong-lesson", task_id=tid,
                              text="A lesson long enough to count as one.")
        assert m.task_memory__forget(slug="wrong-lesson")["forgotten"] is True
        assert "error" in m.task_memory__get(slug="wrong-lesson")


class TestStaleMemoryNudge:
    """stale_memory_nudge — see taskfw.dispatcher.stale_memory_nudge for the logic itself."""

    def test_nudges_when_a_link_makes_a_memory_contradicted(self):
        tid = create(title="Source")["id"]
        later = create(title="Later")["id"]
        m.task_memory__record(slug="a-lesson", task_id=tid,
                              text="A lesson long enough to count as one.")
        r = m.task_memory__link(slug="a-lesson", task_id=later, relation="contradicted_by")
        assert "stale_memory_nudge" in r

    def test_nudges_when_a_link_makes_a_memory_disputed(self):
        tid = create(title="Source")["id"]
        confirmer = create(title="Confirmer")["id"]
        disputer = create(title="Disputer")["id"]
        m.task_memory__record(slug="a-lesson", task_id=tid,
                              text="A lesson long enough to count as one.")
        m.task_memory__link(slug="a-lesson", task_id=confirmer, relation="confirmed_by")
        r = m.task_memory__link(slug="a-lesson", task_id=disputer, relation="contradicted_by")
        assert "stale_memory_nudge" in r

    def test_no_nudge_on_a_confirmed_only_link(self):
        tid = create(title="Source")["id"]
        later = create(title="Later")["id"]
        m.task_memory__record(slug="a-lesson", task_id=tid,
                              text="A lesson long enough to count as one.")
        r = m.task_memory__link(slug="a-lesson", task_id=later, relation="confirmed_by")
        assert "stale_memory_nudge" not in r

    def test_stateless_refires_on_an_idempotent_relink(self):
        tid = create(title="Source")["id"]
        later = create(title="Later")["id"]
        m.task_memory__record(slug="a-lesson", task_id=tid,
                              text="A lesson long enough to count as one.")
        m.task_memory__link(slug="a-lesson", task_id=later, relation="contradicted_by")
        r = m.task_memory__link(slug="a-lesson", task_id=later, relation="contradicted_by")
        assert "stale_memory_nudge" in r


class TestRegistration:
    @pytest.mark.anyio
    async def test_every_tool_is_registered_with_the_server(self):
        names = {t.name for t in await m.mcp.list_tools()}
        assert {
            "tasks__context", "tasks__get", "tasks__list", "tasks__search",
            "tasks__create", "tasks__update", "tasks__check_item", "tasks__finish",
            "tasks__add_decision", "tasks__link", "tasks__unlink", "tasks__edges",
            "tasks__add_commit", "tasks__set_active", "tasks__active", "tasks__clear_active",
            "tasks__grooming_accuracy",
            "task_memory__record", "task_memory__recall", "task_memory__get",
            "task_memory__link", "task_memory__supersede", "task_memory__forget",
        } <= names

    @pytest.mark.anyio
    async def test_every_tool_has_a_description(self):
        for tool in await m.mcp.list_tools():
            assert tool.description, f"{tool.name} has no description"


class TestToolCallLogging:
    """Coverage for task:58782207: every registered tool must go through
    tool_called, so 'no tool was missed' is a checked fact, not a claim.

    Called with no arguments — most raise TypeError for a missing required
    argument, but that happens INSIDE tool_called's wrapped call (whether via
    the `logged` decorator or a hook-bearing tool's own inner tool_called),
    so it still produces exactly one `tool=<name>` log line. Whether that
    call succeeds, refuses, or errors is irrelevant to this test.
    """

    @pytest.mark.anyio
    async def test_every_registered_tool_logs_exactly_once_when_called(self, caplog):
        import logging

        for tool in await m.mcp.list_tools():
            fn = getattr(m, tool.name)
            caplog.clear()
            with caplog.at_level(logging.INFO, logger="taskfw"):
                try:
                    fn()
                except Exception:
                    pass
                lines = [line for line in caplog.text.splitlines() if f"tool={tool.name} " in line]
            assert lines, f"{tool.name} produced no tool_called log line"
            assert len(lines) == 1, f"{tool.name} logged {len(lines)} times, expected exactly 1"


@pytest.fixture
def anyio_backend():
    return "asyncio"
