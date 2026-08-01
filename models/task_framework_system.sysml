package TaskFrameworkSystem {
    doc /* Structural model of task-framework: five parts over a shared
         * Foundation.
         *
         * WHY THE DECOMPOSITION LOOKS LIKE THIS. A rule and the data it governs
         * must belong to the same part. Split them — the rule in an interface
         * layer, the data in the store — and the store can no longer be lifted
         * out on its own, because taking it means leaving the rules behind.
         * Every requirement here therefore has exactly one satisfying part, and
         * tests/test_models.py fails the build if a second appears.
         *
         * BEFORE DESIGNING A FIX FOR A COUPLING, CHECK WHETHER THE COUPLED
         * THING NEEDS TO EXIST. This decomposition began with two such split
         * requirements and solved neither: after simplification one became
         * trivial (with two issue types, the hierarchy rule is "an epic has no
         * parent") and the other stopped being a rule at all (with no
         * pre-commit gate, commit linkage is observation, not enforcement).
         * The coupling dissolved rather than being resolved — most of it was
         * complexity that did not need to exist. That is the most transferable
         * lesson in this model.
         *
         * NO SINGLE POINT OF FAILURE, and that is designed rather than
         * incidental. A daemon owning store, rules, and ephemeral context was
         * specified and then abandoned: SQLite already provides the
         * cross-process sharing that justified it, while it would add an HTTP
         * hop to every tool call, a supervised process to keep alive, and a
         * hard dependency for every host. If a daemon is proposed again, that
         * is the argument it has to answer. */

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

    part def HostAdapter {
        doc /* The only host-specific part, and optional by construction:
             * everything the framework does is reachable through TaskMCPTools,
             * so a host with no adapter loses automaticity and nothing else.
             *
             * DELIBERATELY NOT NAMED FOR ONE HOST. An adapter covers only what
             * a pull interface structurally cannot do for itself — noticing
             * events the framework does not own. Claude Code is the shipped
             * instance (taskfw/hooks/claude_code.py) and adds two behaviours,
             * both observational and independently switchable: a one-line
             * active-task pointer, and commit capture. A second host would be
             * another instance, not another part.
             *
             * THE CONTRACT BELONGS TO THE ABSTRACTION, not to any instance. No
             * adapter may block a tool call or reject a write; fail-open is
             * absolute. An adapter that observes rather than enforces will miss
             * events, so anything it captures must be recoverable by other
             * means — taskfw/backfill.py rebuilds commit links from git history,
             * which is what makes silent failure acceptable rather than
             * quietly lossy. An adapter with no recovery path is not
             * permitted. */
        part foundation : Foundation;
        part rules : LifecycleRules;
        part store : TaskStore;
    }

    part def System {
        part foundation : Foundation;
        part store : TaskStore;
        part rules : LifecycleRules;
        part mcpTools : TaskMCPTools;
        part hooks : HostAdapter;
    }
}
