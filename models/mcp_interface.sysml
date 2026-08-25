package McpInterface {
    doc /* interface def / port def structure for the taskfw MCP server's tool
         * surface — the agent<->server contract, transcribed from the ~35
         * @_tool-registered functions in taskfw/mcp_server.py.
         *
         * One port def per tool-name prefix (tasks__, concept__,
         * task_memory__), each declaring one item per tool so the identifier
         * list is a checkable claim, not prose. One interface def per port
         * def, connecting an agent end to a server end — modelling that
         * every call crosses the same boundary in both directions (a tool
         * call out, a dict result back), not a one-way broadcast.
         *
         * Kept honest the same way task_lifecycle.sysml and
         * derived_values.sysml are: not by executing anything here, but by
         * TestMcpInterfaceMatchesCode in tests/test_sysml.py, which
         * enumerates the LIVE tool registry via m.mcp.list_tools() — the
         * same call test_mcp_tools.py::TestRegistration already uses — and
         * asserts the port items here match it exactly. A hand-copied name
         * list would be a second unenforced restatement of the registry;
         * this is the failure mode the paired test exists to catch.
         *
         * NOT MODELLED, deliberately:
         *   - Lifecycle validation. Which tools are read vs write, and that
         *     every write goes through taskfw.lifecycle.check_save/
         *     check_transition/check_link_rel before taskfw.store, is a
         *     behavioural contract documented in the mcp-portable-interface
         *     concept, not a structural fact about the RPC surface itself.
         *     Modelling it here would make this file look like it captures
         *     the full contract when it only captures tool names and which
         *     family each belongs to.
         *   - Per-tool argument/return shapes. Each tool's signature is
         *     already the source of truth in mcp_server.py; duplicating it
         *     here would be exactly the kind of second home for a rule this
         *     framework's CLAUDE.md warns against.
         *   - Nudges/hooks (taskfw/dispatcher/). Advisory response annotations
         *     riding on a subset of tool results are a behaviour of specific
         *     tools, not a structural feature of the ports those tools sit
         *     on. */

    private import Foundation::ModelProvenance;

    @ModelProvenance {
        derivedFromCommit = "a3860a4a1c74c0ec0a2db6808ffd5d2023f5f744";
        derivedAt = "2026-08-23";
        modelledPaths = (
            "taskfw/mcp_server.py"
        );
    }

    port def TasksPort {
        doc /* One item per tasks__* tool registered in taskfw/mcp_server.py. */
        item tasks__context;
        item tasks__grooming_accuracy;
        item tasks__logs;
        item tasks__log_skill_invocation;
        item tasks__get;
        item tasks__phase;
        item tasks__list;
        item tasks__search;
        item tasks__create;
        item tasks__update;
        item tasks__check_item;
        item tasks__finish;
        item tasks__add_introspection;
        item tasks__add_decision;
        item tasks__link;
        item tasks__unlink;
        item tasks__edges;
        item tasks__format_commit_message;
        item tasks__add_commit;
        item tasks__set_active;
        item tasks__active;
        item tasks__clear_active;
    }

    interface def TasksInterface {
        doc /* The agent<->server boundary for the tasks__* family. */
        end agent : TasksPort;
        end server : TasksPort;
    }

    port def ConceptPort {
        doc /* One item per concept__* tool registered in taskfw/mcp_server.py. */
        item concept__list;
        item concept__get;
        item concept__modules;
        item concept__search;
        item concept__upsert;
        item concept__delete;
        item concept__uncovered;
    }

    interface def ConceptInterface {
        doc /* The agent<->server boundary for the concept__* family. */
        end agent : ConceptPort;
        end server : ConceptPort;
    }

    port def TaskMemoryPort {
        doc /* One item per task_memory__* tool registered in taskfw/mcp_server.py. */
        item task_memory__record;
        item task_memory__recall;
        item task_memory__get;
        item task_memory__link;
        item task_memory__supersede;
        item task_memory__forget;
    }

    interface def TaskMemoryInterface {
        doc /* The agent<->server boundary for the task_memory__* family. */
        end agent : TaskMemoryPort;
        end server : TaskMemoryPort;
    }
}
