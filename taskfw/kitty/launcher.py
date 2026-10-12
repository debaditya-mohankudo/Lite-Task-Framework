"""Launcher: `start` / `resume` / `list` / `plain`. Always opens a NEW tab; a tab's task is fixed.

`plain` is the exception that proves the rule: a tab that belongs to no task (e.g. to run a project
skill). It carries no task identity, no project hooks and no store row, only the caller's PATH.

Env carries identity (TASKFW_TASK_ID/WORKTREE/DB) and the caller's PATH (kitty from the Dock
has a stripped PATH, so a bare `python3` would be 3.9). The project hooks are injected with
`claude --settings`, which merges with the global hooks.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Mapping

from taskfw.config import DB_PATH

from .adapter import KittyAdapter, KittyError
from .store import Store
from . import worktrees

DEFAULT_HOOKS = Path(__file__).resolve().parent / "hooks.json"
DEFAULT_WATCHER = Path(__file__).resolve().parent / "watcher.py"


def check_install(hooks: str | Path | None = None) -> str | None:
    """Why a launch would produce a broken tab, or None. The hooks file ships inside the package;
    a missing one would start claude with a missing --settings file."""
    hooks = hooks or DEFAULT_HOOKS  # read at call time so the check follows the module value
    if not Path(hooks).is_file():
        return (f"hooks file {str(hooks)!r} not found: "
                "reinstall task-framework")
    return None


def resolve_worktree(task_id: str, explicit: str | None, isolate: bool = False) -> str:
    """The cwd for a new task tab. An explicit path wins and is used as-is; otherwise the cwd,
    unless isolate (opt-in) asks for the task's own git worktree of the repo containing the cwd
    (created or reused). Outside a git repo, isolate falls back to the cwd."""
    if explicit:
        return explicit
    repo = worktrees.repo_root(os.getcwd()) if isolate else None
    return str(worktrees.ensure(repo, task_id)) if repo else os.getcwd()


class NoSessionToResume(KittyError):
    """resume was asked for a task whose claude session id was never backfilled."""


def build_env(task_id: str, worktree: str, db: str, path: str, store: str | None = None) -> dict[str, str]:
    env = {"TASKFW_TASK_ID": task_id, "TASKFW_WORKTREE": worktree, "TASKFW_DB": db, "PATH": path,
           "KITTY_TASKFW_PYTHON": sys.executable}  # the watcher's writer needs a Python with sqlite3
    if store and store != ":memory:":
        env["KITTY_TASKFW_STORE"] = store  # so hooks backfill the same store the launcher wrote
    return env


def claude_cmd(claude: str, hooks: str | Path | None, resume_session: str | None = None,
               prompt: str | None = None) -> list[str]:
    """argv for the tab's claude. The prompt is one trailing argv element after `--` (kitty runs
    argv with no shell, so quotes and newlines need no escaping; `--` keeps a leading '-' a prompt)."""
    cmd = [claude, *(["--settings", str(hooks)] if hooks else [])]
    if resume_session:
        cmd += ["--resume", resume_session]
    if prompt:
        cmd += ["--", prompt]
    return cmd


def plain_tab(adapter: KittyAdapter, cwd: str, prompt: str | None = None, title: str | None = None,
              *, claude: str | None = None, path: str | None = None) -> int:
    """A claude tab in `cwd` that belongs to no task; returns its window id. cwd is the directory
    whose `.claude/skills` the tab will see."""
    cwd = str(Path(cwd).expanduser().resolve())
    if not Path(cwd).is_dir():
        raise KittyError(f"cwd {cwd!r} is not a directory")
    return adapter.launch(cwd=cwd, env={"PATH": path if path is not None else os.environ.get("PATH", "")},
                          cmd=claude_cmd(claude or shutil.which("claude") or "claude", None, None, prompt),
                          title=title)


class Launcher:
    def __init__(self, adapter: KittyAdapter, store: Store, *, hooks: str | Path = DEFAULT_HOOKS,
                 watcher: str | Path | None = DEFAULT_WATCHER,
                 claude: str | None = None, db: str | None = None, path: str | None = None):
        self.adapter, self.store, self.hooks = adapter, store, hooks
        self.watcher = str(watcher) if watcher else None
        self.claude = claude or shutil.which("claude") or "claude"
        self.db = str(Path(db).expanduser()) if db else str(DB_PATH)
        self.path = path if path is not None else os.environ.get("PATH", "")

    def _launch(self, task_id: str, worktree: str, resume_session: str | None,
                prompt: str | None = None) -> int:
        worktree = str(Path(worktree).expanduser().resolve())
        wid = self.adapter.launch(
            cwd=worktree,
            cmd=claude_cmd(self.claude, self.hooks, resume_session, prompt),
            env=build_env(task_id, worktree, self.db, self.path, self.store.path),
            user_vars={"task_id": task_id},
            title=f"task:{task_id}",
            watcher=self.watcher,
        )
        # launch returns a WINDOW id; resolve the tab id by asking kitty (match on id).
        found = self.adapter.ls(match=f"id:{wid}")
        tab_id = found[0].tab_id if found else None
        self.store.record_launch(task_id=task_id, window_id=wid, tab_id=tab_id,
                                 socket=self.adapter.socket, cwd=worktree)
        return wid

    def start(self, task_id: str, worktree: str, prompt: str | None = None) -> int:
        return self._launch(task_id, worktree, None, prompt)

    def resume(self, task_id: str, worktree: str | None = None, fresh: bool = False,
               prompt: str | None = None) -> int:
        last = self.store.latest_for_task(task_id)
        sid = self.store.latest_session_for_task(task_id)
        if sid is None and not fresh:
            raise NoSessionToResume(
                f"task {task_id}: no claude session id recorded (session died before "
                "SessionStart backfilled it); pass --fresh to start a new session")
        wt = worktree or (last.cwd if last else os.getcwd())
        return self._launch(task_id, wt, sid, prompt)

    def list(self) -> list[tuple[str, int, str]]:
        """(task_id, window_id, state) for open rows; state is the live `state` user var,
        or 'gone' if kitty no longer has the window."""
        live = {w.id: w for w in self.adapter.ls()}
        out = []
        for r in self.store.list(open_only=True):
            w = live.get(r.window_id)
            out.append((r.task_id, r.window_id, w.user_vars.get("state", "-") if w else "gone"))
        return out


def main(argv: list[str] | None = None, *, env: Mapping[str, str] = os.environ) -> int:
    ap = argparse.ArgumentParser(prog="taskfw-kitty")
    ap.add_argument("--socket", default=env.get("KITTY_LISTEN_ON"),
                    help="kitty remote-control socket (default: $KITTY_LISTEN_ON)")
    ap.add_argument("--store", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("start", "resume"):
        p = sub.add_parser(name)
        p.add_argument("task_id")
        p.add_argument("--worktree", default=None, help="explicit cwd; default: the current directory")
        p.add_argument("--prompt", default=None, help="initial prompt handed to claude as its argument")
        if name == "start":
            p.add_argument("--isolate", action="store_true",
                           help="run in a git worktree of this repo derived from the task id")
        if name == "resume":
            p.add_argument("--fresh", action="store_true")
    sub.add_parser("list")
    pl = sub.add_parser("plain", help="open a claude tab that belongs to no task; prints the window id")
    pl.add_argument("--cwd", default=None, help="directory holding the .claude/skills to use; default: the current directory")
    pl.add_argument("--prompt", default=None, help="initial prompt handed to claude as its argument")
    pl.add_argument("--title", default=None, help="tab title")
    o = sub.add_parser("open", help="open a task in a tab if kitty allows; always prints JSON, exits 0")
    o.add_argument("task_id")
    o.add_argument("--worktree", default=None)
    o.add_argument("--prompt", default=None, help="initial prompt handed to claude as its argument")
    o.add_argument("--isolate", dest="isolate", action="store_true", default=None,
                   help="run in a git worktree of the --worktree directory's repo, derived from the task id")
    o.add_argument("--no-isolate", dest="isolate", action="store_false",
                   help="never isolate; default: isolate only when another live tab shares the repo")
    c = sub.add_parser("cleanup", help="remove a task's worktree and branch; refuses dirty or unmerged work")
    c.add_argument("task_id")
    a = ap.parse_args(argv)
    if a.cmd == "cleanup":
        repo = worktrees.repo_root(os.getcwd())
        if not repo:
            print("error: not inside a git repository", file=sys.stderr)
            return 1
        try:
            print(worktrees.remove(repo, a.task_id))
        except worktrees.WorktreeError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        return 0
    if a.cmd == "open":  # socket optional here and nothing below may fail the caller
        from .opener import open_task
        # the directory is only a base here: the opener isolates it (or not) once it knows the live tabs
        print(json.dumps(open_task(a.task_id, a.worktree or os.getcwd(), socket=a.socket, store=a.store,
                                   env=env, prompt=a.prompt, isolate=a.isolate).as_dict()))
        return 0
    if not a.socket:
        ap.error("no kitty socket: pass --socket or run inside kitty")
    if a.cmd == "plain":  # no hooks file, no store: check_install and Store do not apply
        try:
            print(plain_tab(KittyAdapter(a.socket), a.cwd or os.getcwd(), a.prompt, a.title))
        except KittyError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        return 0
    bad = check_install()
    if bad:
        print(f"error: {bad}", file=sys.stderr)
        return 1
    store = Store(a.store) if a.store else Store()
    try:
        lau = Launcher(KittyAdapter(a.socket), store)
        if a.cmd == "start":
            print(lau.start(a.task_id, resolve_worktree(a.task_id, a.worktree, a.isolate), a.prompt))
        elif a.cmd == "resume":
            print(lau.resume(a.task_id, a.worktree, a.fresh, a.prompt))
        else:
            for t, w, s in lau.list():
                print(f"{t}\twindow={w}\t{s}")
    except (KittyError, worktrees.WorktreeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
