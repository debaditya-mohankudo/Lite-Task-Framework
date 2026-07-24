"""FastMCP dispatcher for claude-hooks MCP server.

Maps domain → (module, [actions]). Each action becomes a tool named domain__action.
Add new tool modules here as they are migrated in.
"""
from __future__ import annotations

import importlib
import importlib.util
import inspect
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

# Ensure src/ is on the path for relative imports within tool modules
_SRC = Path(__file__).resolve().parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


def _load_module(module_path: str):
    """Load a module by dotted name (relative to src/) or absolute file path."""
    return importlib.import_module(module_path)


def _wrap(domain: str, handler):
    is_async = inspect.iscoroutinefunction(handler)
    if is_async:
        async def wrapped(**kwargs):
            return await handler(**kwargs)
    else:
        def wrapped(**kwargs):
            return handler(**kwargs)
    wrapped.__name__ = handler.__name__
    wrapped.__doc__ = handler.__doc__
    wrapped.__wrapped__ = handler
    return wrapped


DOMAIN_MAP: dict[str, tuple[str, list[str]]] = {
    "hooks":   ("tools.hooks",   ["checkpoint_query", "read_logs_sqlite", "server_memory", "session_id"]),
    "memory":  ("tools.memory",  ["add", "add_batch", "search", "list", "get", "list_domains",
                                  "tool_hints", "read_compact", "delete"]),
    "tasks":   ("tools.tasks",   ["create", "create_scaffolded", "create_epic", "create_feedback", "list", "get", "update", "delete", "search",
                                  "set_active", "clear_active", "pop_active", "finish",
                                  "log_event", "history", "add_decision", "pause",
                                  "neighbors", "index_task", "link_tasks", "edges", "get_commits"]),
    "code_rag": ("tools.code_rag", ["query", "smart_search", "index_files"]),
    "diff_rag": ("tools.diff_rag", ["query", "smart_search", "index_commits"]),
    "think":    ("tools.think",    ["think"]),
    "scratch":  ("tools.scratch",  ["set", "get", "list", "delete", "clear"]),
    "concept":  ("tools.concept",  ["get", "list", "upsert", "delete", "modules"]),
}


def build_dispatcher(mcp: FastMCP) -> None:
    for domain, (module_path, actions) in DOMAIN_MAP.items():
        module = _load_module(module_path)
        for action in actions:
            handler = getattr(module, f"handle_{action}")
            mcp.tool(name=f"{domain}__{action}")(_wrap(domain, handler))
