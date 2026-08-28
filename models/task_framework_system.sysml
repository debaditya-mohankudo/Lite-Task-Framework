package TaskFrameworkSystem {
    doc /* Structural model of task-framework: four parts over a shared
         * Foundation.
         *
         * WHY THE DECOMPOSITION LOOKS LIKE THIS. A rule and the data it governs
         * must belong to the same part. Split them — the rule in an interface
         * layer, the data in the store — and the store can no longer be lifted
         * out on its own, because taking it means leaving the rules behind.
         * Every requirement here therefore has exactly one satisfying part, and
         * tests/test_sysml.py fails the build if a second appears.
         *
         * BEFORE DESIGNING A FIX FOR A COUPLING, CHECK WHETHER THE COUPLED
         * THING NEEDS TO EXIST. This decomposition began with three such split
         * requirements and solved none of them by splitting harder: after
         * simplification one became trivial (with the issue "type" reduced to a
         * single `epic` boolean, the hierarchy rule is "an epic has no
         * parent"), one stopped being a rule
         * at all (with no pre-commit gate, commit linkage was observation, not
         * enforcement), and the third — the HostAdapter part that used to sit
         * here, injecting a one-line active-task pointer on every prompt — was
         * removed outright once every host-specific behaviour it existed to
         * carry either duplicated what a skill already does explicitly
         * (tasks__context) or could simply be a skill's own step
         * (tasks__add_commit, called from .claude/skills/commit/skill.md). The
         * coupling dissolved rather than being resolved each time — most of it
         * was complexity that did not need to exist. That is the most
         * transferable lesson in this model.
         *
         * PULL, NOT PUSH, ALL THE WAY DOWN. Removing HostAdapter is not a
         * missing feature — it is the framework becoming consistent with its
         * own stated principle. The adapter's own module docstring admitted
         * "this IS prompt injection, just one line of it" before it was
         * deleted; a system that says context is never pushed should not carry
         * an acknowledged exception to that rule as one of its four parts.
         *
         * NO SINGLE POINT OF FAILURE, and that is designed rather than
         * incidental. A daemon owning store, rules, and ephemeral context was
         * specified and then abandoned: SQLite already provides the
         * cross-process sharing that justified it, while it would add an HTTP
         * hop to every tool call, a supervised process to keep alive, and a
         * hard dependency for every host. If a daemon is proposed again, that
         * is the argument it has to answer. */

    private import Foundation::Foundation;
    private import Foundation::ModelProvenance;

    @ModelProvenance {
        derivedFromCommit = "f04a0b0ed8e1e099fb7f1a17af1e9817005a09fb";
        derivedAt = "2026-08-27";
        modelledPaths = (
            "taskfw/store.py",
            "taskfw/task.py",
            "taskfw/lifecycle.py",
            "taskfw/mcp_server.py",
            "taskfw/context.py",
            "taskfw/backfill.py",
            "taskfw/dispatcher/__init__.py",
            "taskfw/dispatcher/chassis.py",
            "taskfw/dispatcher/phase.py",
            "taskfw/dispatcher/nudges.py"
        );
    }

    part def TaskStore {
        doc /* taskfw/store.py, taskfw/task.py. A task is ONE typed object
             * serialised to JSON. Data access only — no rule enforcement, so
             * LifecycleRules keeps a single allocation. A store method rejects
             * only what it structurally cannot do, never a policy violation. */
        part foundation : Foundation;
    }

    part def LifecycleRules {
        doc /* taskfw/lifecycle.py. The single implementation of every task
             * rule, imported by the interface part. Pure functions over
             * (inputs, store) — no session, prompt, or host state, which is
             * what lets every caller share them.
             *
             * The project's central claim, that rules cannot be bypassed,
             * rests on this part having exactly one implementation and the
             * interface depending on it. It is a structural claim, visible in
             * the composition below rather than asserted in prose. */
        part foundation : Foundation;
    }

    part def TaskMCPTools {
        doc /* taskfw/mcp_server.py, taskfw/context.py, taskfw/dispatcher/.
             * The ONLY interface: MCP is the portability layer, so any MCP
             * host reaches the whole framework here with no host-specific
             * adapter on top of it.
             *
             * In-process — tools import the library directly. tasks__context is
             * the headline tool and the sole replacement for per-turn prompt
             * injection. tasks__add_commit is called explicitly by a host's
             * commit skill after a successful commit, rather than observed
             * automatically — nothing enforces that call happens, which is why
             * taskfw/backfill.py exists as the recovery path: it rebuilds
             * commit links from git history regardless of whether the call was
             * ever made.
             *
             * taskfw/dispatcher/ (chassis.py, phase.py, nudges.py — split for
             * organisation only, everything re-exported unchanged from the
             * package __init__.py) adds advisory, non-blocking annotations to a
             * few tools' results — deliberately NOT a fifth part:
             *   - introspection_nudge (tasks__add_introspection): an
             *     introspection report cites a lesson but the task has never
             *     promoted one to loop memory.
             *   - finish_nudge (tasks__finish): a task closed with no
             *     introspection report yet.
             *   - finish_reminder_nudge (tasks__check_item / tasks__update):
             *     the resolution checklist is 100% done but the task is still
             *     open.
             *   - stale_memory_nudge (task_memory__link): the memory being
             *     linked is 'disputed' or 'contradicted'.
             *   - ungroomed_progress_nudge (tasks__check_item / tasks__update):
             *     checklist progress exists but task.grooming is still empty.
             *   - loop_debt_nudge / task_debt_nudge (tasks__set_active): the
             *     earlier of the two calls every documented workflow makes at
             *     the start of work, not tasks__context or tasks__create —
             *     surfaces, respectively, how many recent finished tasks
             *     graded no risks at all, and whether the task just made
             *     active carries ungraded risks of its own. Both derive from
             *     taskfw/accuracy.py:_task_grading, the same per-task
             *     classification tasks__grooming_accuracy uses, so neither
             *     nudge can disagree with what that tool reports (task:07f9270c).
             * A nudge never refuses a write and never changes what
             * is stored, so it carries none of the properties that earned
             * LifecycleRules or TaskStore their own part: it enforces nothing
             * and guarantees nothing observable from outside a tool's result.
             * It lives here as a documented behaviour of the interface layer,
             * the same way tasks__context's trim/truncation behaviour is
             * documented in this part's doc rather than given a part of its
             * own: whole sections drop least-useful-first, except grooming,
             * which is trimmed field-by-field before it is dropped whole —
             * see PullContextRequirement.
             *
             * ONE TOOL IS THE EXCEPTION TO "dispatcher never changes what is
             * stored": tasks__check_item(done=True) also sets its task as
             * the active task for the current scope as a direct side
             * effect — a checked-off item is unambiguous evidence
             * implementation started, so it removes the "forgot to call
             * set_active" failure mode instead of merely nudging about it.
             * Checking off a checklist's LAST open item goes further and
             * finishes the task outright, through the same path
             * tasks__finish uses. Neither set nor finish needs a gate to
             * mirror: the active-task pointer is a single in-memory dict
             * entry per scope (task:f5ace343) — not persisted, not a stack —
             * so setting a different task simply overwrites whatever was
             * active; there is nothing to protect against losing, and
             * nothing survives a server restart. tasks__finish and
             * tasks__clear_active just clear the pointer — there is no
             * underlying task to restore. This mutation lives in
             * mcp_server.py itself, not taskfw/dispatcher/ — it is a tool's own
             * write, gated by LifecycleRules like any other, not an
             * advisory annotation. */
        part foundation : Foundation;
        part rules : LifecycleRules;
        part store : TaskStore;
    }

    part def System {
        doc /* The whole framework, composed for Requirements.sysml to
             * satisfy against: every requirement there is allocated to
             * exactly one of this part's four children. */
        part foundation : Foundation;
        part store : TaskStore;
        part rules : LifecycleRules;
        part mcpTools : TaskMCPTools;
    }
}
