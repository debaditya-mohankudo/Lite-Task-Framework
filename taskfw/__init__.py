"""task-framework — portable task tracking for AI coding agents.

Importable library first, MCP server second. Every caller — the MCP tools,
the optional Claude Code hooks, a script — imports the same store and the
same rules, so there is exactly one implementation of each rule and no way
to reach the database around it.
"""

__version__ = "0.1.0"
