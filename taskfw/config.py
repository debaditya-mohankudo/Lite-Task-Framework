"""Configuration — every tunable in one place, all overridable by environment.

The DB path is deliberately configurable rather than derived from the repo:
a task store may be global (one across all projects) or per-workspace, and
that choice belongs to the operator, not to the code.
"""
from __future__ import annotations

import os
from pathlib import Path

#: Where the task database lives. TASKFW_DB overrides.
DB_PATH = Path(os.environ.get("TASKFW_DB", Path.home() / ".taskfw" / "tasks.db")).expanduser()

#: Log level for the taskfw logger.
LOG_LEVEL = os.environ.get("TASKFW_LOG_LEVEL", "INFO").upper()

#: How long a writer waits for a competing writer's lock before giving up.
#: Several processes (one MCP server per host window, plus hook invocations)
#: write this file concurrently, so this is load-bearing, not decorative.
BUSY_TIMEOUT_MS = int(os.environ.get("TASKFW_BUSY_TIMEOUT_MS", "5000"))


def db_path() -> Path:
    """Return the DB path, creating its parent directory if needed."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return DB_PATH
