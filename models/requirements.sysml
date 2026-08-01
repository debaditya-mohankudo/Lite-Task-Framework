package Requirements {
    doc /* Requirements traced to the parts that satisfy them.
         *
         * ACCEPTANCE CRITERION, and the whole reason this file exists:
         * NO REQUIREMENT MAY BE SATISFIED BY MORE THAN ONE PART.
         *
         * A second `satisfy` statement means a rule and its data have drifted
         * apart — a design defect to fix in code, not a modelling inconvenience
         * to route around. The predecessor system had exactly two such dual
         * allocations, and they are why its task framework could not be
         * extracted. tests/test_models.py enforces this mechanically, because
         * an acceptance criterion nobody runs is just a comment.
         *
         * SOURCING: each requirement cites the code that implements it. None is
         * aspirational — every one below is satisfied by shipped, tested code.
         *
         * DELIBERATELY ABSENT, dropped along with the subsystems that motivated
         * them: checkpoint non-persistence (no runtime state to lose) and
         * per-domain memory scoring (no memory store). */

    private import TaskFrameworkSystem::*;

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
             * processes — one MCP server per host window, plus hook
             * invocations — write the same file concurrently. */
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
             * implementation, imported by both interface parts. The guarantee
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
        doc /* Source: taskfw/lifecycle.py:check_parent. An epic has no parent;
             * a task cannot be its own parent. That is the entire hierarchy.
             *
             * This is the descendant of the predecessor's Jira hierarchy
             * matrix, which needed five issue types and a parent-child table.
             * With two types it reduces to one sentence — the requirement
             * survived, most of its complexity did not. */
    }
    satisfy requirement ParentRuleRequirement by system.rules;

    requirement def SchemaValidationReplacesBodyGateRequirement {
        doc /* Source: taskfw/models.py. A task is a typed object, so
             * "required sections are present" is schema validation and
             * "resolution must be a checklist" is unrepresentable — resolution
             * IS a list.
             *
             * Recorded as a requirement because it names a rule that no longer
             * needs a home: the predecessor needed a body-format gate plus a
             * scaffolding tool to work around it. Neither exists here. */
    }
    satisfy requirement SchemaValidationReplacesBodyGateRequirement by system.store;

    requirement def DerivedProgressRequirement {
        doc /* Source: taskfw/models.py:Task.progress. Progress is computed from
             * the resolution list on every read, never stored, so it cannot
             * drift from the list it counts and needs no reconciliation. */
    }
    satisfy requirement DerivedProgressRequirement by system.store;

    requirement def EdgeLifecycleRequirement {
        doc /* Source: taskfw/store.py:link/unlink. Edges are idempotent to
             * create AND removable. The predecessor could create and read
             * edges but never delete them, so stale edges pointing at abandoned
             * tasks accumulated with no way to clear them. */
    }
    satisfy requirement EdgeLifecycleRequirement by system.store;

    requirement def PullContextRequirement {
        doc /* Source: taskfw/context.py. tasks__context returns the whole
             * working bundle in one call — the sole replacement for per-turn
             * prompt injection. Fixed section order, a documented trim order
             * that drops whole sections least-useful-first, and a `truncated`
             * list so an omitted section is never indistinguishable from an
             * empty one. The task itself is never trimmed. */
    }
    satisfy requirement PullContextRequirement by system.mcpTools;

    requirement def HostPortabilityRequirement {
        doc /* Source: taskfw/mcp_server.py. MCP is the portability layer: any
             * MCP host reaches the whole framework through these tools, with no
             * neutral abstraction and no host-specific adapter required. */
    }
    satisfy requirement HostPortabilityRequirement by system.mcpTools;

    requirement def CommitTaskTraceabilityRequirement {
        doc /* Source: taskfw/hooks/claude_code.py:handle_post_tool_use.
             * A commit referencing task:<id> is linked to that task by
             * observation after the fact.
             *
             * SINGLE allocation, where the predecessor needed two. There is no
             * pre-commit gate, so this is observation rather than enforcement
             * and is therefore not a rule — which is exactly why it does not
             * also belong to LifecycleRules. Dropping the gate is what
             * collapsed the dual allocation. */
    }
    satisfy requirement CommitTaskTraceabilityRequirement by system.hooks;

    requirement def HookFailOpenRequirement {
        doc /* Source: taskfw/hooks/claude_code.py:main, taskfw/backfill.py.
             * A hook never blocks and never raises: malformed input, unknown
             * events, and handler exceptions all yield an empty result and
             * exit 0.
             *
             * backfill is part of this requirement rather than a separate
             * convenience. Failing open on an observational hook loses data
             * invisibly — a task with no commits looks identical to one whose
             * commits were missed — so the recovery path is what makes the
             * silence acceptable. */
    }
    satisfy requirement HookFailOpenRequirement by system.hooks;
}
