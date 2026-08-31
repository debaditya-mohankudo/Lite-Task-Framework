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
        derivedFromCommit = "8bef93c9eb55247a4560afa1ea277e4c85585208";
        derivedAt = "2026-08-31";
        modelledPaths = (
            "taskfw/scope.py",
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
             *   - finish_reminder_nudge (tasks__check_item / tasks__update,
             *     and tasks__add_decision on a call that resolved an item):
             *     the resolution checklist is 100% done but the task is still
             *     open.
             *   - stale_memory_nudge (task_memory__link): the memory being
             *     linked is 'disputed' or 'contradicted'.
             *   - ungroomed_progress_nudge (tasks__check_item / tasks__update,
             *     and tasks__add_decision on a call that resolved an item):
             *     checklist progress exists but task.grooming is still empty.
             *     Both checklist nudges reach add_decision only on the resolve
             *     path, keyed off `progress` being present in the result: a
             *     plain decision changes no checklist state, so nudging about
             *     checklist state there would be noise on nearly every call.
             *   - loop_debt_nudge / task_debt_nudge (tasks__set_active AND
             *     tasks__create — every activation path, task:356b3ada; the
             *     hook resolves the task from result.active or result.id):
             *     surfaces, respectively, how many recent finished tasks
             *     graded no risks at all, and whether the task just made
             *     active carries ungraded risks of its own. task_debt_nudge
             *     is always silent on a fresh tasks__create (no risks yet),
             *     so only loop_debt_nudge speaks there — which is the point:
             *     finishing a task and immediately creating the next one no
             *     longer skips the debt reminder now that the trailing
             *     tasks__set_active is discouraged (task:1105f979). Both
             *     derive from
             *     taskfw/accuracy.py:_task_grading, the same per-task
             *     classification tasks__grooming_accuracy uses, so neither
             *     nudge can disagree with that tool about what COUNTS as
             *     graded (task:07f9270c). They can now differ on WHICH TASKS
             *     were counted, and deliberately so (task:a5bb2f45):
             *     loop_debt_nudge passes the derived Scope, because it fires
             *     on every activation and a debt figure driven by
             *     another repository's tasks would interrupt work here with
             *     no way to tell; tasks__grooming_accuracy stays global by
             *     default, because it is an occasional, deliberate read where
             *     a cross-project pattern is a finding rather than noise.
             *     Neither is ambiguous about it — both name the scope they
             *     counted in their result, and a scoped count additionally
             *     reports the unscoped remainder it left out.
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
             * TICKING A CHECKLIST ITEM DOES NOT TOUCH THE ACTIVE POINTER
             * (task:c88b8730). It once did — a checked-off item is
             * unambiguous evidence implementation started, so it stood in
             * for a "forgot to call set_active". That safety net became
             * redundant once tasks__create activates the new task
             * (task:1105f979) and the task-implementation skill calls
             * tasks__set_active when work begins in a fresh session; the
             * silent repoint it caused — ticking an item on task B while A
             * was active would steal the pointer — cost more clarity than
             * the vanished failure mode was worth. Ticking a checklist's
             * LAST open item still finishes the task outright, through the
             * same path tasks__finish uses — and, since task:1105f979, that
             * path no longer clears the active pointer, so a task finished
             * this way stays active for introspection.
             *
             * Two tools reach the shared ticking path, not one
             * (task:1f400ecf): tasks__check_item, and tasks__add_decision
             * when given a `resolves` index naming the item the decision
             * answers. Both call _apply_check_item, so neither can drift
             * from the other on what ticking does — including not activating.
             *
             * tasks__create sets its new task active (task:1105f979):
             * creation is the start of a loop pass, so activation belongs
             * there rather than as a separate tasks__set_active every caller
             * must remember. epic tasks activate the same way — there is no
             * epic branch. The active-task pointer is a single in-memory
             * dict entry per scope (task:f5ace343) — not persisted, not a
             * stack — so setting a different task simply overwrites whatever
             * was active; there is nothing to protect against losing, and
             * nothing survives a server restart. Once set, NOTHING
             * auto-clears the pointer: neither tasks__finish / checklist
             * auto-finish nor tasks__update reaching a terminal status
             * touches it, so a finished task stays named through
             * introspection — the window loop_debt_nudge and the
             * introspection flow depend on. tasks__clear_active, called
             * explicitly (the task-introspection skill does so at its final
             * step), is the sole deactivation path. That mutation lives in
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
