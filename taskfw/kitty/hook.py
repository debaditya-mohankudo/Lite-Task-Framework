"""Hook bridge: Claude Code hook events -> kitty user vars, store backfill, task context.

Runs as `python -m taskfw.kitty.hook` from hooks/settings.json. The event arrives as JSON on
stdin. It acts only when TASKFW_TASK_ID is set (the tab's task is fixed at launch), and it
never fails the session: any error is swallowed and the process exits 0.

State user var (queryable with `--match var:state=...`):
  SessionStart / Stop / Notification -> waiting   (the session wants the user)
  UserPromptSubmit                   -> running
  SessionEnd                         -> done
`last_active` (epoch seconds) is stamped on SessionStart/UserPromptSubmit/Stop only. Notification
is excluded: it fires while the session sits idle and would keep resetting the idle clock.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from typing import IO, Mapping

from .adapter import KittyAdapter, normalize_socket
from .offload import maybe_offload
from .store import Store

EVENT_STATE = {
    "SessionStart": "waiting",
    "UserPromptSubmit": "running",
    "Stop": "waiting",
    "Notification": "waiting",
    "SessionEnd": "done",
}


STAMP_EVENTS = {"SessionStart", "UserPromptSubmit", "Stop"}


def load_context(db_path: str, task_id: str) -> str | None:
    """Compact task context from taskfw's DB (read-only). None if the task is not found."""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2)
    try:
        row = con.execute("SELECT status, title, data FROM tasks WHERE id=?", (task_id,)).fetchone()
    finally:
        con.close()
    if row is None:
        return None
    status, title, raw = row
    items = (json.loads(raw or "{}").get("resolution")) or []
    done = sum(1 for i in items if i.get("done"))
    lines = [f"Task {task_id} [{status}]: {title}", f"Progress: {done}/{len(items)}"]
    lines += [f"- [ ] {i['text']}" for i in items if not i.get("done")]
    return "\n".join(lines)


def handle(payload: dict, env: Mapping[str, str], *, adapter: KittyAdapter | None = None,
           store: Store | None = None) -> dict | None:
    """Process one hook event. Returns the hook JSON output, or None for no output."""
    # Offload runs for every session carrying this hook, task tab or not.
    blocked = maybe_offload(payload, env, adapter=adapter)
    if blocked:
        return blocked
    task_id = env.get("TASKFW_TASK_ID")
    event = payload.get("hook_event_name")
    if not task_id or not event:
        return None

    socket, window = env.get("KITTY_LISTEN_ON"), env.get("KITTY_WINDOW_ID")
    if socket and window and window.isdigit():
        wid = int(window)
        if event == "SessionStart" and payload.get("session_id"):
            (store or Store()).backfill_session(
                socket=normalize_socket(socket), window_id=wid, session_id=payload["session_id"])
        state = EVENT_STATE.get(event)
        if state:
            ad = adapter or KittyAdapter(socket, check_version=False)
            vars_ = {"state": state}
            if event in STAMP_EVENTS:
                vars_["last_active"] = str(int(time.time()))
            if event == "UserPromptSubmit":
                vars_["prompted"] = "1"  # claude only saves a transcript after a first prompt
            ad.set_user_vars(wid, vars_)

    if event == "UserPromptSubmit" and env.get("TASKFW_DB"):
        ctx = load_context(env["TASKFW_DB"], task_id)
        if ctx:
            return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": ctx}}
    return None


def main(stdin: IO[str] = sys.stdin, env: Mapping[str, str] = os.environ) -> int:
    try:
        out = handle(json.load(stdin), env)
        if out:
            print(json.dumps(out))
    except Exception as e:  # never break the session
        print(f"kitty-taskfw hook: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
