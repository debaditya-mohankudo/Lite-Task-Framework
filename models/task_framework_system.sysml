package TaskFrameworkSystem {
    doc /* Structural model of task-framework: five parts over a shared
         * Foundation.
         *
         * WHY THE DECOMPOSITION LOOKS LIKE THIS. The predecessor system
         * (claude-hooks) allocated two requirements to two parts each — the
         * RULE lived in a hook gate while the DATA lived in the task store, so
         * taking the store meant losing the rules. That dual allocation is why
         * its task framework could not be lifted out cleanly, and it is the
         * defect this decomposition exists to avoid.
         *
         * WHAT ACTUALLY HAPPENED TO THAT DEFECT, worth recording because it is
         * the most transferable lesson here: after simplification, one of those
         * requirements became trivial (two issue types, so "an epic has no
         * parent") and the other stopped being a rule at all (with no
         * pre-commit gate, commit linkage is observation). The coupling was not
         * so much solved as dissolved — most of it was complexity that did not
         * need to exist. Before designing a fix for a coupling, check whether
         * the coupled thing needs to exist.
         *
         * NO SINGLE POINT OF FAILURE, and that is designed rather than
         * incidental. An earlier revision put a FastAPI daemon at the centre
         * owning store, rules, and ephemeral context; it was abandoned once
         * verification showed SQLite already provided the cross-process sharing
         * it was justified by, while it added an HTTP hop on every tool call, a
         * supervised process, and a hard dependency for every host. If a daemon
         * is proposed again, that is the argument it has to answer. */

    private import Foundation::Foundation;

    part def TaskStore {
        doc /* taskfw/store.py, taskfw/models.py. A task is ONE typed object
             * serialised to JSON. Data access only — no rule enforcement, so
             * LifecycleRules keeps a single allocation. A store method rejects
             * only what it structurally cannot do, never a policy violation. */
        part foundation : Foundation;
    }

    part def LifecycleRules {
        doc /* taskfw/lifecycle.py. The single implementation of every task
             * rule, imported by both interface parts. Pure functions over
             * (inputs, store) — no session, prompt, or host state, which is
             * what lets every caller share them.
             *
             * The project's central claim, that rules cannot be bypassed,
             * rests on this part having exactly one implementation and both
             * interfaces depending on it. It is a structural claim, visible in
             * the composition below rather than asserted in prose. */
        part foundation : Foundation;
    }

    part def TaskMCPTools {
        doc /* taskfw/mcp_server.py, taskfw/context.py. The portable interface:
             * MCP is the portability layer, so any MCP host reaches the whole
             * framework here with no neutral abstraction on top.
             *
             * In-process — tools import the library directly. tasks__context is
             * the headline tool and the sole replacement for per-turn prompt
             * injection. */
        part foundation : Foundation;
        part rules : LifecycleRules;
        part store : TaskStore;
    }

    part def ClaudeCodeHooks {
        doc /* taskfw/hooks/claude_code.py, taskfw/backfill.py. The only
             * host-specific part, and optional: everything is reachable through
             * MCP tools, so a host without hooks loses automaticity and nothing
             * else.
             *
             * Two behaviours, both observational and independently switchable:
             * a one-line active-task pointer, and commit capture. Neither can
             * block a tool call. Fail-open is absolute, and backfill from git
             * history is what makes that recoverable rather than quietly
             * lossy. */
        part foundation : Foundation;
        part rules : LifecycleRules;
        part store : TaskStore;
    }

    part def System {
        part foundation : Foundation;
        part store : TaskStore;
        part rules : LifecycleRules;
        part mcpTools : TaskMCPTools;
        part hooks : ClaudeCodeHooks;
    }
}
