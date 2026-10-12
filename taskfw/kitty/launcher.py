"""Launcher: `start` / `resume` / `list` / `plain`. Always opens a NEW tab; a tab's task is fixed.

`plain` is the exception that proves the rule: a tab that belongs to no task (e.g. to run a project
skill). It carries no task identity, no project hooks and no store row, only the caller's PATH.

Env carries identity (TASKFW_TASK_ID/WORKTREE/DB) and the caller's PATH (kitty from the Dock
has a stripped PATH, so a bare `python3` would be 3.9). The project hooks are injected with
`claude --settings`, which merges with the global hooks.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Mapping

from taskfw.config import DB_PATH
from taskfw.kitty_open import bad_parent, report_back
from taskfw.log import get_logger

from .adapter import KittyAdapter, KittyError
from .store import Store
from . import worktrees

DEFAULT_HOOKS = Path(__file__).resolve().parent / "hooks.json"
DEFAULT_WATCHER = Path(__file__).resolve().parent / "watcher.py"

log = get_logger("kitty")


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


SLIM_MCP_DEFAULT = ("taskfw", "claude-hooks")
CLAUDE_JSON = Path("~/.claude.json")
SETTINGS_JSON = Path("~/.claude/settings.json")
MCP_READ_TRIES, MCP_READ_PAUSE = 3, 0.05  # a read racing a write of ~/.claude.json sees a torn file


def mcp_choice(env: Mapping[str, str] = os.environ,
               claude_json: str | Path = CLAUDE_JSON) -> tuple[str | None, str]:
    """(`--mcp-config` JSON | None, source). The JSON holds only the servers a task tab uses, copied from
    the user-level `mcpServers` in ~/.claude.json (their commands are host paths, so nothing is checked
    in). Names come from TASKFW_KITTY_MCP (comma list; empty or `all` = no slimming), default taskfw +
    claude-hooks. The config is None, meaning the current launch, when the file is unreadable or any named
    server is absent: a partial set would leave a tab without tools it was promised. Strict means ONLY these:
    project `.mcp.json`, `projects[...].mcpServers` and plugin servers are dropped by design; name one in
    TASKFW_KITTY_MCP only if it is user-level, or set `all`. `source` says why, for
    the launch log: `default`, `override`, `all-requested`, or `fallback:<reason>`; an override naming no
    server (`,`) falls back to the default set as `fallback:override-empty`, never to an empty one. A
    malformed parse is retried (a concurrent write looks malformed) before it is reported."""
    raw = env.get("TASKFW_KITTY_MCP")
    if raw is not None and raw.strip().lower() in ("", "all"):
        return None, "all-requested"
    names = [n.strip() for n in raw.split(",") if n.strip()] if raw else []
    source = "override" if names else "default"
    if raw and not names:  # e.g. ',': an override naming nothing is not an override, and never an empty set
        source = "fallback:override-empty"
    names = names or list(SLIM_MCP_DEFAULT)
    path, why = Path(claude_json).expanduser(), ""
    for attempt in range(MCP_READ_TRIES):
        try:
            config = json.loads(path.read_text())
            if "mcpServers" not in config:  # looked up apart from the names: a server may be called mcpServers
                return None, "fallback:no-mcpServers"
            servers = config["mcpServers"]
            return json.dumps({"mcpServers": {n: servers[n] for n in names}}), source
        except OSError:
            why = "claude-json-unreadable"
            break
        except (ValueError, TypeError):
            why = "claude-json-malformed"  # a write racing this read looks the same, so look again
            if attempt + 1 < MCP_READ_TRIES:
                time.sleep(MCP_READ_PAUSE)
        except KeyError as e:
            why = f"server-absent={e.args[0]}"
            break
    return None, f"fallback:{why}"


def dropped_servers(cwd: str | Path, claude_json: str | Path = CLAUDE_JSON,
                    settings: str | Path = SETTINGS_JSON) -> list[str]:
    """What `--strict-mcp-config` hides from a tab in `cwd`, for the launch log: `.mcp.json` files in cwd
    or a parent, `projects[cwd].mcpServers` in ~/.claude.json, and enabled plugins. Best effort: an
    unreadable or malformed file contributes nothing, because this only annotates a launch."""
    def load(p):
        try:
            return json.loads(Path(p).expanduser().read_text())
        except (OSError, ValueError):
            return {}
    cwd = Path(cwd)
    out = [f"{d / '.mcp.json'}" for d in (cwd, *cwd.parents) if (d / ".mcp.json").is_file()]
    proj = load(claude_json).get("projects")
    if isinstance(proj, dict) and isinstance(proj.get(str(cwd)), dict):
        out += [f"project:{n}" for n in (proj[str(cwd)].get("mcpServers") or {})]
    plugins = load(settings).get("enabledPlugins")
    if isinstance(plugins, dict):
        out += [f"plugin:{n}" for n, on in plugins.items() if on]
    return out


def claude_cmd(claude: str, hooks: str | Path | None, resume_session: str | None = None,
               prompt: str | None = None, mcp_config: str | None = None) -> list[str]:
    """argv for the tab's claude. The prompt is one trailing argv element after `--` (kitty runs
    argv with no shell, so quotes and newlines need no escaping; `--` keeps a leading '-' a prompt).
    `mcp_config` (a path to a JSON file, never inline: server env/headers would show in `ps`) restricts the
    tab to exactly those MCP servers, connectors included."""
    cmd = [claude, *(["--settings", str(hooks)] if hooks else [])]
    if mcp_config:
        cmd += ["--strict-mcp-config", "--mcp-config", mcp_config]
    if resume_session:
        cmd += ["--resume", resume_session]
    if prompt:
        cmd += ["--", prompt]
    return cmd


def write_mcp_config(config: str, directory: Path) -> Path:
    """`config` saved 0600 under `directory`, named by its content hash so relaunches share one file.
    It holds the servers' env/headers, so it is a file (like ~/.claude.json) and never argv. Every call
    writes a 0600 temp file and renames it into place, so the name never holds a partial write, and a
    truncated file from an interrupted launch is replaced rather than reused."""
    path = directory / f"mcp-{hashlib.sha256(config.encode()).hexdigest()[:16]}.json"
    tmp = None
    try:
        directory.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".mcp-", suffix=".tmp")  # mkstemp is 0600
        with os.fdopen(fd, "w") as f:
            f.write(config)
        os.replace(tmp, path)
    except OSError as e:  # never fall back to inline JSON: that leaks the secrets this exists to hide
        if tmp:
            Path(tmp).unlink(missing_ok=True)
        raise KittyError(f"cannot write MCP config to {path}: {e}") from e
    return path


def plain_tab(adapter: KittyAdapter, cwd: str, prompt: str | None = None, title: str | None = None,
              *, claude: str | None = None, path: str | None = None, parent: str | None = None) -> int:
    """A claude tab in `cwd` that belongs to no task; returns its window id. cwd is the directory
    whose `.claude/skills` the tab will see. `parent` is the caller's session name: given, the prompt
    ends with the shared report-back sentence, so the tab messages the caller when it finishes.
    Unlike a task tab, a plain tab is never slimmed: no mcp_choice and no check_install, so it gets every
    MCP server its directory and the user config define (that project has its own MCP needs)."""
    cwd = str(Path(cwd).expanduser().resolve())
    if not Path(cwd).is_dir():
        raise KittyError(f"cwd {cwd!r} is not a directory")
    if bad := bad_parent(parent or ""):
        raise KittyError(bad)
    if parent:
        prompt = " ".join(filter(None, [prompt, report_back(parent, "the outcome in one line")]))
    wid = adapter.launch(cwd=cwd, env={"PATH": path if path is not None else os.environ.get("PATH", "")},
                         cmd=claude_cmd(claude or shutil.which("claude") or "claude", None, None, prompt),
                         title=title)
    log.info("kitty plain window=%s cwd=%s", wid, cwd)
    return wid


class Launcher:
    def __init__(self, adapter: KittyAdapter, store: Store, *, hooks: str | Path = DEFAULT_HOOKS,
                 watcher: str | Path | None = DEFAULT_WATCHER,
                 claude: str | None = None, db: str | None = None, path: str | None = None,
                 mcp_config: str | None = None, mcp_source: str = "none"):
        self.adapter, self.store, self.hooks = adapter, store, hooks
        self.mcp_config = mcp_config  # None (the default, and what tests get) = every MCP server
        self.mcp_source = mcp_source  # why that choice, from mcp_choice; only the launch log reads it
        self.watcher = str(watcher) if watcher else None
        self.claude = claude or shutil.which("claude") or "claude"
        self.db = str(Path(db).expanduser()) if db else str(DB_PATH)
        self.path = path if path is not None else os.environ.get("PATH", "")
        self.claude_json, self.settings_json = CLAUDE_JSON, SETTINGS_JSON  # where _drops looks
        self.mcp_dir = Path(self.db).parent / "mcp"  # where the slim MCP config file goes

    def _drops(self, cwd: str) -> str:
        """`;drops=...` naming what the slim set hides in `cwd`, so a default launch does not read as complete."""
        dropped = dropped_servers(cwd, self.claude_json, self.settings_json) if self.mcp_config else []
        return f";drops={','.join(dropped)}" if dropped else ""

    def _launch(self, task_id: str, worktree: str, resume_session: str | None,
                prompt: str | None = None) -> int:
        worktree = str(Path(worktree).expanduser().resolve())
        wid = self.adapter.launch(
            cwd=worktree,
            cmd=claude_cmd(self.claude, self.hooks, resume_session, prompt,
                           str(write_mcp_config(self.mcp_config, self.mcp_dir)) if self.mcp_config else None),
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
        log.info("kitty launch task=%s resumed_session=%s window=%s cwd=%s mcp=%s mcp_source=%s",
                 task_id, resume_session or "-", wid, worktree,
                 ",".join(json.loads(self.mcp_config)["mcpServers"]) if self.mcp_config else "all",
                 self.mcp_source + self._drops(worktree))
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
    pl.add_argument("--parent", default=None,
                    help="caller's session name (as ListAgents prints it); the tab SendMessages it when finished")
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
            print(plain_tab(KittyAdapter(a.socket), a.cwd or os.getcwd(), a.prompt, a.title, parent=a.parent))
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
        mcp, mcp_source = mcp_choice(env)
        lau = Launcher(KittyAdapter(a.socket), store, mcp_config=mcp, mcp_source=mcp_source)
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
