"""LifecycleRules — the single implementation of every task rule.

This module exists so each rule has exactly ONE home. The system this project
replaces kept rules in hook gates while the data lived in the task DB, which
meant taking the data meant losing the rules — two parts satisfying one
requirement. Everything here is imported by the MCP tools and by the optional
hooks, so there is no second copy and no way to reach the store around it.

Every function is pure over (inputs, store): no session, prompt, or host state.
That is what lets any caller use it without dragging a runtime along.

DELIBERATELY SMALL, and smaller than it first looked. Three rules were removed
rather than relocated:

  - Body-template validation: gone. With a typed task object, "required
    sections are present" is schema validation.
  - Hierarchy matrix: gone. Two issue types mean one sentence, not a matrix.
  - Commit traceability: moved out. With no pre-commit gate, commit-to-task
    linkage is observation, not enforcement, so it is not a rule at all and
    lives in the hook that observes it.

What is left is a state machine and a parent rule.
"""
from __future__ import annotations

from dataclasses import dataclass

from taskfw.models import STATUSES, TASK_TYPES, Task

#: Transitions, transcribed from the state model rather than inferred.
#: `done` and `abandoned` are terminal. Any non-terminal state may go to
#: `abandoned`, handled in check_transition rather than listed per state.
TRANSITIONS: dict[str, set[str]] = {
    "open": {"done", "blocked", "abandoned"},
    "blocked": {"open", "abandoned"},
    "done": set(),
    "abandoned": set(),
}

TERMINAL = {"done", "abandoned"}


@dataclass(frozen=True)
class Decision:
    """The result of a rule check.

    Structured rather than a (bool, str) tuple because the callers differ —
    MCP tools, hooks, and scripts each render a refusal differently, and `rule`
    lets them do that without string-matching the reason.
    """
    allowed: bool
    rule: str = ""
    reason: str = ""

    def __bool__(self) -> bool:
        return self.allowed

    @classmethod
    def ok(cls) -> "Decision":
        return cls(True)

    @classmethod
    def deny(cls, rule: str, reason: str) -> "Decision":
        return cls(False, rule, reason)


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------

def check_status(status: str) -> Decision:
    if status not in STATUSES:
        return Decision.deny("status", f"Unknown status {status!r}. Valid: {', '.join(STATUSES)}.")
    return Decision.ok()


def check_type(task_type: str) -> Decision:
    if task_type not in TASK_TYPES:
        return Decision.deny("type", f"Unknown type {task_type!r}. Valid: {', '.join(TASK_TYPES)}.")
    return Decision.ok()


def check_transition(current: str, target: str) -> Decision:
    """Enforce the state machine.

    Same-status is always allowed so that saving a task without changing its
    status is never a transition error.
    """
    for status in (current, target):
        bad = check_status(status)
        if not bad:
            return bad
    if current == target:
        return Decision.ok()
    if target == "abandoned":
        # Anything may be abandoned — special-cased rather than listed against
        # every state, matching how the state model expresses it.
        return Decision.ok() if current not in TERMINAL else Decision.deny(
            "transition", f"Task is already {current}; terminal states cannot transition."
        )
    if target not in TRANSITIONS[current]:
        allowed = sorted(TRANSITIONS[current] | ({"abandoned"} if current not in TERMINAL else set()))
        return Decision.deny(
            "transition",
            f"Cannot move from {current!r} to {target!r}. "
            + (f"Allowed: {', '.join(allowed)}." if allowed else f"{current!r} is terminal."),
        )
    return Decision.ok()


def check_parent(task_type: str, parent: Task | None) -> Decision:
    """The whole hierarchy rule: an epic has no parent.

    With only two types there is nothing else to say. A task may have an epic
    parent or none; nesting a task under a task is allowed because forbidding
    it would buy nothing a user cannot express with an epic.
    """
    if task_type == "epic" and parent is not None:
        return Decision.deny("parent", "An epic cannot have a parent.")
    return Decision.ok()


def check_save(task: Task, *, previous: Task | None = None, parent: Task | None = None) -> Decision:
    """Every rule that applies to writing a task, in one call.

    The single place a mutation is validated. Callers should not compose the
    individual checks themselves — doing so is how one caller ends up enforcing
    a different set than another.
    """
    for check in (check_type(task.type), check_status(task.status)):
        if not check:
            return check
    parent_check = check_parent(task.type, parent)
    if not parent_check:
        return parent_check
    if task.parent == task.id:
        return Decision.deny("parent", "A task cannot be its own parent.")
    if previous is not None:
        return check_transition(previous.status, task.status)
    return Decision.ok()
