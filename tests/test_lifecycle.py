"""LifecycleRules tests — the state machine and the parent rule.

These are pure functions, so the tests need no store and no fixtures. That is
itself the property worth protecting: the moment a rule needs a session or a
host, it stops being importable by every caller.
"""
from __future__ import annotations

import pytest

from taskfw.lifecycle import (
    TERMINAL,
    TRANSITIONS,
    Decision,
    check_event_kind,
    check_link_rel,
    check_parent,
    check_save,
    check_status,
    check_transition,
)
from taskfw.task import TASK_EDGE_RELATIONS, TASK_EVENT_KINDS, TASK_STATUSES, Task


class TestDecision:
    def test_is_truthy_when_allowed(self):
        assert Decision.ok()
        assert not Decision.deny("r", "why")

    def test_deny_carries_rule_and_reason(self):
        d = Decision.deny("transition", "nope")
        assert d.rule == "transition" and d.reason == "nope"


class TestTransitions:
    @pytest.mark.parametrize(
        "current,target",
        [("open", "done"), ("open", "blocked"), ("blocked", "open"), ("open", "abandoned")],
    )
    def test_allowed(self, current, target):
        assert check_transition(current, target)

    @pytest.mark.parametrize("current", ["open", "blocked"])
    def test_anything_non_terminal_may_be_abandoned(self, current):
        assert check_transition(current, "abandoned")

    @pytest.mark.parametrize("state", sorted(TERMINAL))
    def test_terminal_states_cannot_move_to_a_different_state(self, state):
        assert not check_transition(state, "open")
        assert not check_transition(state, "blocked")

    @pytest.mark.parametrize("state", sorted(TERMINAL))
    def test_same_status_beats_the_terminal_rule(self, state):
        """Precedence matters: re-saving a terminal task unchanged is not a transition.

        Two rules overlap here — "same status is always allowed" and "terminal
        states cannot transition". Same-status wins, so saving an abandoned task
        without touching its status is never an error. Pinned because the
        opposite precedence is an easy and very annoying regression.
        """
        assert check_transition(state, state)

    def test_blocked_cannot_go_straight_to_done(self):
        d = check_transition("blocked", "done")
        assert not d and d.rule == "transition"
        assert "open" in d.reason  # tells the caller the way out

    def test_same_status_is_always_allowed(self):
        for s in TASK_STATUSES:
            assert check_transition(s, s)

    def test_unknown_status_is_rejected(self):
        assert not check_transition("open", "sideways")
        assert not check_transition("sideways", "open")

    def test_transition_table_covers_every_status(self):
        assert set(TRANSITIONS) == set(TASK_STATUSES)


class TestParentRule:
    def test_epic_cannot_have_a_parent(self):
        d = check_parent(True, Task(epic=True))
        assert not d and d.rule == "parent"

    def test_epic_without_parent_is_fine(self):
        assert check_parent(True, None)

    def test_task_may_have_a_parent_or_not(self):
        assert check_parent(False, Task(epic=True))
        assert check_parent(False, None)


class TestStatus:
    def test_status_validation(self):
        assert check_status("open")
        assert not check_status("in_progress")


class TestCheckLinkRel:
    def test_accepts_every_relation_in_the_closed_set(self):
        for rel in TASK_EDGE_RELATIONS:
            assert check_link_rel(rel)

    def test_rejects_an_unrecognised_relation(self):
        d = check_link_rel("made_up_rel")
        assert not d and d.rule == "rel" and "made_up_rel" in d.reason

    def test_rejects_the_excluded_parent_of(self):
        """parent_of is deliberately not in the closed set — redundant with Task.parent."""
        assert not check_link_rel("parent_of")


class TestCheckEventKind:
    def test_accepts_every_kind_in_the_closed_set(self):
        for kind in TASK_EVENT_KINDS:
            assert check_event_kind(kind)

    def test_rejects_an_unrecognised_kind(self):
        d = check_event_kind("freeform_label")
        assert not d and d.rule == "event_kind" and "freeform_label" in d.reason


class TestCheckSave:
    def test_accepts_a_plain_new_task(self):
        assert check_save(Task(title="t"))

    def test_rejects_self_parenting(self):
        t = Task(title="t")
        t.parent = t.id
        d = check_save(t)
        assert not d and d.rule == "parent"

    def test_enforces_the_transition_when_previous_is_given(self):
        prev = Task(title="t")
        prev.status = "done"
        nxt = Task(id=prev.id, title="t")
        nxt.status = "open"
        d = check_save(nxt, previous=prev)
        assert not d and d.rule == "transition"

    def test_allows_a_valid_transition(self):
        prev = Task(title="t")
        nxt = Task(id=prev.id, title="t")
        nxt.status = "done"
        assert check_save(nxt, previous=prev)

    def test_epic_with_parent_is_rejected(self):
        e = Task(title="e", epic=True)
        e.parent = "someid"
        assert not check_save(e, parent=Task(epic=True))
