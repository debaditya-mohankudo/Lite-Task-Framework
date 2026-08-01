"""Isolates every test run from the real ~/.taskfw/tasks.db and from `logs`.

Individual tests already avoid touching production data by passing an
explicit path to TaskStore(). This guards the two things that cannot take a
path per call, both process-wide singletons in taskfw.log with no store to
draw a path from — lifecycle.py's check_* functions are pure by design, with
no session or store parameter to thread one through:

- TASKFW_DB redirects tasks__logs' own read path (log_conn(), always
  config.db_path() — see mcp_server.py) away from the real database.
- TASKFW_LOG_JSONL redirects routine logging itself away from SQLite
  entirely for the length of the run — see taskfw/log.py's own docstring for
  why a test run should never add rows to `logs` at all, isolated database
  or not. One file, truncated fresh per run, not accumulated across them.

Both must be set before taskfw.config / taskfw.log are first imported, since
each is read once, at that module's own configure time. conftest.py is loaded
by pytest before any test module is collected, so this has to be the first
thing here — before any import of taskfw itself.
"""
import os
import tempfile
from pathlib import Path

os.environ.setdefault(
    "TASKFW_DB",
    os.path.join(tempfile.mkdtemp(prefix="taskfw-test-session-"), "tasks.db"),
)
os.environ.setdefault(
    "TASKFW_LOG_JSONL",
    str(Path(__file__).parent / ".taskfw-test-logs.jsonl"),
)
