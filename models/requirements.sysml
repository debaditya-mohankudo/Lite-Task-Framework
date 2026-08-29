package Requirements {
    doc /* Requirements traced to the parts that satisfy them.
         *
         * ACCEPTANCE CRITERION, and the whole reason this file exists:
         * NO REQUIREMENT MAY BE SATISFIED BY MORE THAN ONE PART.
         *
         * A second `satisfy` statement means a rule and its data have drifted
         * apart — a design defect to fix in code, not a modelling inconvenience
         * to route around. A requirement split across two parts cannot be
         * satisfied by taking either one, so neither part is independently
         * useful and the pair has to move together forever.
         * tests/test_sysml.py enforces this mechanically, because an
         * acceptance criterion nobody runs is just a comment.
         *
         * SOURCING: each requirement cites the code that implements it. None is
         * aspirational — every one below is satisfied by shipped, tested code.
         *
         * DELIBERATELY ABSENT, dropped along with the subsystems that motivated
         * them: checkpoint non-persistence (no runtime state to lose) and
         * per-domain memory scoring (no scoring, no injection).
         *
         * NO EXTERNAL DEPENDENCY, decided rather than inherited. The framework
         * calls no other MCP server — not for memory, code search, or history
         * search — and must not: a framework that needs a second server to
         * close its own loop is not portable, which is the whole claim. The
         * scope test is end-to-end sufficiency — a capability earns a place
         * here only if create/groom/implement/introspect cannot close without
         * it. Codebase and history search are the agent's own tools, not the
         * framework's, and do not meet that test.
         *
         * LOOP MEMORY DOES meet it, and the distinction is the point rather
         * than a softening of the rule. A GENERAL memory store — arbitrary
         * facts across arbitrary domains, retrieved by score and injected —
         * remains out of scope and is what the absent note above still
         * refuses. What is in scope is narrower: the lessons introspection
         * itself produces and no other part can hold. Graded risks live in a
         * task's grooming, decisions in its events, architectural facts in the
         * concept store; a constraint learned the hard way and a technique
         * worth reusing belong to no single task and no single module, and
         * before this had nowhere to go. See LoopMemoryEvidenceRequirement. */

    private import TaskFrameworkSystem::*;
    private import Foundation::ModelProvenance;

    @ModelProvenance {
        derivedFromCommit = "a1e255ad384cca021e125e1096463a03720cbb40";
        derivedAt = "2026-08-29";
        modelledPaths = (
            "taskfw/scope.py",
            "taskfw/db/schema.py",
            "taskfw/db/connect.py",
            "taskfw/log.py",
            "taskfw/lifecycle.py",
            "taskfw/task.py",
            "taskfw/store.py",
            "taskfw/memory.py",
            "taskfw/context.py",
            "taskfw/accuracy.py",
            "taskfw/risk.py",
            "taskfw/mcp_server.py",
            "taskfw/backfill.py",
            "taskfw/dispatcher/__init__.py",
            "taskfw/dispatcher/chassis.py",
            "taskfw/dispatcher/phase.py",
            "taskfw/dispatcher/nudges.py",
            ".claude/skills/commit/skill.md"
        );
    }

    part system : System;

    requirement def AdditiveOnlyMigrationRequirement {
        doc /* Source: taskfw/db/schema.py:migrate. Migrations are additive
             * only — CREATE TABLE IF NOT EXISTS and ALTER TABLE ADD COLUMN are
             * the only statements the function can emit. A column removed from
             * TABLES is reported as orphaned and left in place.
             *
             * Enforced by construction rather than by policy: there is no code
             * path that drops, so a destructive migration cannot be written by
             * accident. */
    }
    satisfy requirement AdditiveOnlyMigrationRequirement by system.foundation;

    requirement def ConcurrentWriterSafetyRequirement {
        doc /* Source: taskfw/db/connect.py:connect. WAL, busy_timeout, and
             * foreign_keys are set in one place, never by callers. Several
             * processes — one MCP server per host window, plus taskfw-backfill
             * runs — write the same file concurrently. */
    }
    satisfy requirement ConcurrentWriterSafetyRequirement by system.foundation;

    requirement def ProtocolSafeLoggingRequirement {
        doc /* Source: taskfw/log.py. All logging goes to stderr. An MCP server
             * speaks JSON-RPC over stdout, so a stray write corrupts the
             * protocol. A correctness constraint, not a style preference, and
             * pinned by tests/test_logging.py. */
    }
    satisfy requirement ProtocolSafeLoggingRequirement by system.foundation;

    requirement def SingleRuleImplementationRequirement {
        doc /* Source: taskfw/lifecycle.py. Every task rule has exactly one
             * implementation, imported by the interface part. The guarantee
             * that rules cannot be bypassed comes from single-sourcing the
             * rule, NOT from a transport boundary — conflating the two is what
             * motivated an abandoned daemon design. */
    }
    satisfy requirement SingleRuleImplementationRequirement by system.rules;

    requirement def TaskStateMachineRequirement {
        doc /* Source: taskfw/lifecycle.py:check_transition, modelled in
             * TaskLifecycle. done and abandoned are terminal; any non-terminal
             * state may be abandoned; same-status is allowed and takes
             * precedence over the terminal rule, which is what makes finishing
             * an already-finished task idempotent. */
    }
    satisfy requirement TaskStateMachineRequirement by system.rules;

    requirement def ParentRuleRequirement {
        doc /* Source: taskfw/lifecycle.py:check_parent (an epic has no
             * parent) and check_save (a task cannot be its own parent).
             * That is the entire hierarchy.
             *
             * Recorded as a requirement because of how little it takes to
             * satisfy. A Jira-style set of five issue types needs a matrix of
             * which type may parent which, and a table to hold it; a single
             * `epic` boolean (there is no "type" enum — one flag, not two
             * named values) reduces the same requirement to one sentence with
             * no matrix at all. The rule survived the simplification, almost
             * none of its machinery did — check_parent takes a bool now, and
             * check_type stopped existing because a bool cannot be malformed. */
    }
    satisfy requirement ParentRuleRequirement by system.rules;

    requirement def SchemaValidationReplacesBodyGateRequirement {
        doc /* Source: taskfw/task.py. A task is a typed object, so
             * "required sections are present" is schema validation and
             * "resolution must be a checklist" is unrepresentable — resolution
             * IS a list.
             *
             * Recorded as a requirement because it names a rule with no home
             * and no need for one. Enforcing the same thing over a free-text
             * body takes a format gate to reject malformed sections, and then
             * a scaffolding tool to generate bodies that satisfy the gate.
             * Neither exists here, because neither has anything left to do. */
    }
    satisfy requirement SchemaValidationReplacesBodyGateRequirement by system.store;

    requirement def DerivedProgressRequirement {
        doc /* Source: taskfw/task.py:Task.progress. Progress is computed from
             * the resolution list on every read, never stored, so it cannot
             * drift from the list it counts and needs no reconciliation. */
    }
    satisfy requirement DerivedProgressRequirement by system.store;

    requirement def EdgeLifecycleRequirement {
        doc /* Source: taskfw/store.py:link/unlink. Edges are idempotent to
             * create AND removable. An edge that can only be added is
             * permanent: a relation to an abandoned or superseded task
             * survives forever, and every later reader has to judge for
             * themselves whether it still means anything. Creation without
             * removal is half a lifecycle. */
    }
    satisfy requirement EdgeLifecycleRequirement by system.store;

    requirement def LoopMemoryEvidenceRequirement {
        doc /* Source: taskfw/memory.py. A loop memory cites the task it was
             * learned from, and later tasks confirm or contradict it. Its
             * standing — unverified, confirmed, disputed, contradicted — is
             * computed from those links on every read and never stored.
             *
             * The same construction as DerivedProgressRequirement and
             * taskfw/accuracy.py: a stored confidence is a self-assessment
             * that drifts from its own evidence, and a computed one cannot.
             * A disputed memory is REPORTED as disputed rather than resolved,
             * because the store cannot know which side is right and returning
             * it as settled would be an omission indistinguishable from an
             * absence.
             *
             * Obsolete memories are superseded, never removed — the row
             * survives and names its replacement, matching the methodology's
             * own rule that stale knowledge is flagged rather than deleted
             * unilaterally. Nothing is injected; a memory reaches an agent
             * only because it called task_memory__recall. */
    }
    satisfy requirement LoopMemoryEvidenceRequirement by system.store;

    requirement def PullContextRequirement {
        doc /* Source: taskfw/context.py. tasks__context returns the whole
             * working bundle in one call — the sole replacement for per-turn
             * prompt injection. Fixed section order, a documented trim order
             * that drops whole sections least-useful-first, and a `truncated`
             * list so an omitted section is never indistinguishable from an
             * empty one. The task itself is never trimmed.
             *
             * GROOMING IS THE ONE EXCEPTION to whole-section dropping: it is
             * trimmed field-by-field, least useful first (GROOMING_TRIM_ORDER),
             * until the bundle fits or grooming has nothing left to give.
             * Fields actually dropped are reported in `grooming_truncated`,
             * distinct from `truncated` — the same omission-must-be-visible
             * discipline applied one level down, because a bundle whose only
             * over-budget section is grooming would otherwise come back with
             * grooming silently gone instead of partially present. Only if
             * grooming empties out entirely does it fall through to a normal
             * whole-section drop and get named in `truncated` like everything
             * else.
             *
             * `lessons` (loop memories matching the task) is a guaranteed
             * section in the bundle, not an optional add-on — the same
             * `_passes_floor` relevance floor used by `_related_candidates`
             * excludes weak matches from both sections rather than always
             * filling to their count cap with whatever is available, however
             * weak.
             *
             * A SECOND filter applies to both, and this is where the two
             * sections stop behaving alike (task:a5bb2f45). `_passes_floor`
             * asks whether a candidate is ABOUT the same thing; `_same_project`
             * asks whether it is FROM the same place, comparing Task.scope.
             * Word overlap could only ever answer the first: a shared tag such
             * as `code-review` supplies two common words, so a task in an
             * unrelated repository clears the floor honestly and is still not
             * a neighbour. `related` therefore EXCLUDES a cross-project
             * candidate outright, while `lessons` RETURNS it carrying
             * `cross_project: true` — loop memory exists to generalise across
             * tasks, so hiding a foreign lesson would defeat the subsystem to
             * enforce a boundary it was built to cross. An unscoped record
             * (every task written before Task.scope existed) is compatible
             * with every scope, so neither filter can retroactively erase the
             * pre-existing corpus. */
    }
    satisfy requirement PullContextRequirement by system.mcpTools;

    requirement def GroomingFeedbackEdgeRequirement {
        doc /* Source: taskfw/accuracy.py:grooming_accuracy. Risk grades are
             * aggregated across finished tasks, so a pattern spanning several
             * executions is readable rather than merely writable.
             *
             * Records the requirement the framework's central claim depends on.
             * Grooming predicts and introspection grades, but the methodology
             * also says repeated `wrong` grades mean grooming is asking the
             * wrong questions — a statement about the SERIES. Grades live
             * inside each task, so nothing could see the series, and the
             * instruction to change grooming itself was unactionable. Per task
             * the loop closed; across tasks it did not.
             *
             * Tallies are derived from the per-risk grades and never read back
             * from an introspection report's self-reported count — the same
             * construction as DerivedProgressRequirement, for the same reason.
             * A stored count can disagree with what it counts.
             *
             * Allocated to mcpTools, not store: this is a derivation over
             * persisted data, exactly as PullContextRequirement is, and the
             * store holds no rules and no analysis.
             *
             * Two additions (task:f24be6e4, task:07f9270c) protect the grades
             * this requirement depends on rather than merely reading them.
             * First, a risk carries a stable, framework-assigned `id`; a
             * re-groom (tasks__update) merges grooming.risks by id instead of
             * replacing the list wholesale, so a grade can no longer be
             * dropped by omitting the risk from a later pass — every other
             * grooming field is unaffected. Recurrence groups by id when
             * present, falling back to normalised text only for risks written
             * before this change. Second, the debt this requirement exists to
             * make readable is now also surfaced proactively: tasks__set_active
             * carries loop_debt_nudge/task_debt_nudge, derived from the same
             * per-task classification (_task_grading) this aggregate uses, so
             * skipped introspection is visible at the start of the next task
             * rather than only on request. */
    }
    satisfy requirement GroomingFeedbackEdgeRequirement by system.mcpTools;

    requirement def HostPortabilityRequirement {
        doc /* Source: taskfw/mcp_server.py. MCP is the portability layer: any
             * MCP host reaches the whole framework through these tools, with no
             * neutral abstraction and no host-specific adapter required. */
    }
    satisfy requirement HostPortabilityRequirement by system.mcpTools;

    requirement def CommitTaskTraceabilityRequirement {
        doc /* Source: taskfw/mcp_server.py:tasks__add_commit,
             * .claude/skills/commit/skill.md, taskfw/backfill.py.
             * A commit referencing task:<id> is linked to that task — by an
             * explicit tasks__add_commit call the commit skill makes after a
             * successful commit, or by backfill scanning git history for
             * whatever that call missed.
             *
             * SINGLE allocation, and the reason is worth stating: nothing
             * enforces the call happens, so this is not a rule LifecycleRules
             * could own — add a pre-commit gate and it would become one,
             * immediately needing a home in both parts. There used to be a
             * host-specific adapter here observing commits automatically; it
             * was removed once nothing distinguished what it did from a skill
             * making the same call on purpose. backfill is part of this
             * requirement rather than a separate convenience: a call that can
             * be skipped, forgotten, or never made at all loses data invisibly
             * — a task with no commits looks identical to one whose commits
             * were never linked — so the recovery path is what makes that
             * acceptable rather than quietly lossy. */
    }
    satisfy requirement CommitTaskTraceabilityRequirement by system.mcpTools;
}
