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
import shutil
import sqlite3
import subprocess
import sys
import time
from typing import IO, Mapping

from .adapter import KittyAdapter, normalize_socket
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


def _session_tty(pid: int | None = None) -> str | None:
    """Controlling tty of the claude session: hooks have no /dev/tty, so walk up the parents."""
    pid = pid or os.getppid()
    for _ in range(8):
        out = subprocess.run(["ps", "-o", "tty=,ppid=", "-p", str(pid)],
                             capture_output=True, text=True, timeout=2).stdout.split()
        if not out:
            return None
        if out[0] not in ("??", "?"):
            return f"/dev/{out[0]}"
        pid = int(out[1])
    return None


def desktop_notify(task_id: str, message: str, *, tty: str | None = None) -> bool:
    """Desktop notification through kitty's notify kitten (kitty >= 0.36, OSC 99).

    `kitten notify` cannot open /dev/tty from a hook, so build the escape code with
    --only-print-escape-code and write it to the session's tty; kitty then shows it. The
    task id is the identifier, so a newer notification for the same tab replaces the old one.
    """
    kitten = shutil.which("kitten")
    tty = tty or _session_tty()
    if not kitten or not tty:
        return False
    code = subprocess.run(
        [kitten, "notify", "--only-print-escape-code", "--app-name", "taskfw",
         "--type", "taskfw.needs_input", "--identifier", f"taskfw-{task_id}",
         "--expire-after", "30s", f"task:{task_id}", message or "needs input"],
        capture_output=True, text=True, timeout=5, check=True).stdout
    # The kitten has no flag for the protocol's occasion key: add o=unfocused to every chunk
    # so kitty shows the notification only while the window is not focused.
    code = code.replace("\x1b]99;", "\x1b]99;o=unfocused:")
    with open(tty, "w") as f:
        f.write(code)
    return True


def handle(payload: dict, env: Mapping[str, str], *, adapter: KittyAdapter | None = None,
           store: Store | None = None) -> dict | None:
    """Process one hook event. Returns the hook JSON output, or None for no output."""
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

    if event == "Notification" and env.get("TASKFW_NOTIFY", "1") != "0":
        try:
            desktop_notify(task_id, payload.get("message", ""))
        except Exception as e:  # a missing notification must never cost the state update
            print(f"kitty-taskfw notify: {e}", file=sys.stderr)

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
