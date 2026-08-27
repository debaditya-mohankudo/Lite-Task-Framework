package TaskLifecycle {
    doc /* State machine for a task, transcribed directly from
         * taskfw/task.py (TASK_STATUSES — the state names) and
         * taskfw/lifecycle.py (TRANSITIONS, TERMINAL, check_transition —
         * the edges). Ground truth, not inferred.
         *
         * New tasks start in openState. doneState and abandonedState are
         * terminal — both map to the empty set in TRANSITIONS. Any
         * non-terminal state may move to abandonedState; that is special-cased
         * in check_transition rather than listed per state, and is modelled
         * here as explicit transitions from every non-terminal state for
         * legibility.
         *
         * NOT MODELLED, deliberately: re-saving a task at its current status.
         * check_transition allows same-status before the terminal rule is
         * reached, so saving an already-done task is a no-op rather than an
         * error — that is what makes tasks__finish idempotent. It is not a
         * transition, so it does not belong in a transition diagram, but the
         * precedence is real and is pinned by test.
         *
         * ALSO NOT MODELLED: closing a parent when all its children finish.
         * That is a cross-task side effect, not a same-task transition. */

    private import Foundation::ModelProvenance;

    @ModelProvenance {
        derivedFromCommit = "a92e0956a6a00838f6f35997c290c43bcecfde8f";
        derivedAt = "2026-08-23";
        modelledPaths = (
            "taskfw/lifecycle.py"
        );
    }

    state def TaskLifecycle {
        doc /* Transcribed from taskfw/lifecycle.py — see the package doc
             * for what is deliberately not modelled here. */
        entry;

        state openState;
        state blockedState;
        state doneState;
        state abandonedState;

        transition openToDone
            first openState
            then doneState;

        transition openToBlocked
            first openState
            then blockedState;

        transition blockedToOpen
            first blockedState
            then openState;

        transition openToAbandoned
            first openState
            then abandonedState;

        transition blockedToAbandoned
            first blockedState
            then abandonedState;
    }
}
