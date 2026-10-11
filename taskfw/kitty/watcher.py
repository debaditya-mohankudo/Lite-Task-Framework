"""kitty watcher: append window lifecycle events for task tabs to the store.

Loaded by kitty itself (`launch --watcher=<this file>`), so it runs inside kitty's bundled
Python on kitty's main thread. Two facts shape it:

* kitty's bundled Python cannot `import sqlite3` (its _sqlite3.so needs a libsqlite3.dylib the
  app bundle lacks), so the watcher never touches the DB. It hands each event to a short-lived
  writer process (fire-and-forget Popen). A locked or slow store therefore cannot block kitty.
* os.environ here is kitty's, not the task window's: the store path and interpreter come from
  the window's child environment (the launcher's --env).

Failures never reach kitty but are not silent: the writer's stderr is appended to a bounded log
(default ~/.kitty-taskfw/watcher-errors.log, override KITTY_TASKFW_ERRORS in the window env),
and a spawn failure in kitty's own process appends one line there. The log is independent of
the store because a wrong or unwritable store is exactly what it must still report.

Stdlib only, every error swallowed, no server hosted here. Windows without a task marker are
ignored before anything is spawned.
"""
from __future__ import annotations

import json
import os
import subprocess
import time

MAX_DATA = 2000
MAX_LOG = 64 * 1024  # one rotation to <log>.1, so the trace is bounded at ~2x this
FALLBACK_PYTHON = "/usr/bin/python3"
DEFAULT_ERRORS = "~/.kitty-taskfw/watcher-errors.log"

# Runs in the child: argv = store, socket, window_id, task_id, kind, data, ts. Any failure
# (including a missing sqlite3) becomes one self-describing stderr line, which lands in the log.
_WRITER = (
    "import sys,time\n"
    "s,k,w,t,kd,d,ts=sys.argv[1:8]\n"
    "try:\n"
    "    import sqlite3\n"
    "    c=sqlite3.connect(s,timeout=5)\n"
    "    c.execute('INSERT INTO events(socket,window_id,task_id,kind,data,ts) VALUES (?,?,?,?,?,?)',"
    "(k,int(w),t,kd,d,float(ts)))\n"
    "    c.commit()\n"
    "except Exception as e:\n"
    "    sys.stderr.write('%s writer failed kind=%s window=%s task=%s store=%s: %s: %s\\n'"
    "%(time.strftime('%Y-%m-%dT%H:%M:%S'),kd,w,t,s,type(e).__name__,e))\n"
    "    sys.exit(1)\n"
)


def _open_log(path: str):
    """Open the error log for append, rotating it first when over MAX_LOG. None if unusable."""
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        if os.path.exists(path) and os.path.getsize(path) > MAX_LOG:
            os.replace(path, path + ".1")
        return open(path, "ab")
    except Exception:
        return None


def _note(path: str, kind: str, window_id, task_id, store: str, exc: BaseException) -> None:
    """Record a failure that happened in kitty's own process (the writer never started)."""
    log = _open_log(path)
    if log is None:
        return
    try:
        with log:
            log.write(("%s writer not started kind=%s window=%s task=%s store=%s: %s: %s\n" % (
                time.strftime("%Y-%m-%dT%H:%M:%S"), kind, window_id, task_id, store,
                type(exc).__name__, exc)).encode("utf-8", "replace"))
    except Exception:
        pass


def _window_env(window) -> dict:
    try:
        return dict(window.child.environ or {})
    except Exception:
        return {}


def _task_id(window, env: dict) -> str | None:
    tid = (getattr(window, "user_vars", None) or {}).get("task_id")
    return str(tid) if tid else (env.get("TASKFW_TASK_ID") or None)


def _socket(boss) -> str:
    s = getattr(boss, "listening_on", None) or ""
    return s if s.startswith(("unix:", "tcp:")) or not s else f"unix:{s}"


def record(boss, window, kind: str, data: dict):
    """Hand one event to a writer process. Returns the Popen (for tests), or None. Never raises."""
    try:
        env = _window_env(window)
        task_id = _task_id(window, env)
        if not task_id:
            return None
        store = os.path.expanduser(env.get("KITTY_TASKFW_STORE", "~/.kitty-taskfw/store.sqlite"))
        py = env.get("KITTY_TASKFW_PYTHON") or FALLBACK_PYTHON
        errors = os.path.expanduser(env.get("KITTY_TASKFW_ERRORS", DEFAULT_ERRORS))
        payload = json.dumps(data, default=str)[:MAX_DATA]
        log = _open_log(errors)
        try:
            return subprocess.Popen(
                [py, "-c", _WRITER, store, _socket(boss), str(int(window.id)), task_id, kind,
                 payload, repr(time.time())],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=log if log is not None else subprocess.DEVNULL,
                start_new_session=True)
        except Exception as e:
            _note(errors, kind, getattr(window, "id", "?"), task_id, store, e)
            return None
        finally:
            if log is not None:
                log.close()  # the child has its own copy; leaking this would exhaust kitty's fds
    except Exception:
        return None


def on_close(boss, window, data):
    record(boss, window, "close", {})


def on_focus_change(boss, window, data):
    record(boss, window, "focus", {"focused": bool(data.get("focused"))})


def on_set_user_var(boss, window, data):
    record(boss, window, "set_user_var", {"key": data.get("key"), "value": data.get("value")})


def on_cmd_startstop(boss, window, data):
    record(boss, window, "cmd_start" if data.get("is_start") else "cmd_stop",
           {"cmdline": data.get("cmdline"), "time": data.get("time")})
