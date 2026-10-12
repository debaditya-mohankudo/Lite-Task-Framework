"""Opener: `open <task_id>` -- put a task in a kitty tab if that is possible, and say why not if not.

Contract (what a caller, e.g. a future taskfw MCP tool, may rely on):
  * open_task never raises and every kitten call is time-bounded (adapter._run).
  * status "opened"     -> a new tab was started exactly as `start` does; window_id is its window.
  * status "exists"     -> a live window already carries this task; nothing is launched.
  * status "not_opened" -> nothing was launched; `reason` says why. That includes a task that has
    had a tab before and has none now: open never resumes, it points at `resume`.
  * `worktree` is the directory the tab was started in and `isolated` says whether it is the task's
    own git worktree. With isolate=None the opener decides: isolated when another live tab (store row
    not closed, window present in kitty) runs in the same repo, else the directory as given; the
    `reason` of an opened result names which. Failing to create the worktree is "not_opened", never
    a fall back into the shared tree.
  * Nothing here sets taskfw's active-task pointer; that lives in the MCP server's memory.

Unlike start/resume, the socket is optional: given none, live kitty sockets are discovered
(the caller's $KITTY_LISTEN_ON, then /tmp/kitty-*). Several live kittys with no way to tell which
is meant is "not_opened", never a guess.
"""
from __future__ import annotations

import glob
import os
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Mapping

from .adapter import KittyAdapter, KittyError, KittyVersionMismatch, normalize_socket
from .launcher import Launcher, check_install, log, mcp_choice
from .store import Store
from . import worktrees

SOCKET_GLOB = "/tmp/kitty-*"


@dataclass(frozen=True)
class OpenResult:
    status: str  # opened | exists | not_opened
    window_id: int | None = None
    socket: str | None = None
    reason: str | None = None
    worktree: str | None = None
    isolated: bool | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def _not_opened(reason: str) -> OpenResult:
    return OpenResult("not_opened", reason=reason)


def open_task(task_id: str, worktree: str | None = None, *, socket: str | None = None,
              store: str | None = None, env: Mapping[str, str] = os.environ,
              adapter_factory: Callable[[str], KittyAdapter] = KittyAdapter,
              socket_glob: str = SOCKET_GLOB, claude: str | None = None,
              prompt: str | None = None, isolate: bool | None = None) -> OpenResult:
    try:
        result = _open(task_id, worktree, socket, store, env, adapter_factory, socket_glob, claude, prompt,
                       isolate)
    except Exception as e:  # the whole point: nothing escapes into the caller
        result = _not_opened(f"unexpected {type(e).__name__}: {e}")
    if result.status != "opened":  # an opened tab is logged by the launcher that launched it
        log.info("kitty open task=%s status=%s reason=%s", task_id, result.status, result.reason)
    return result


def _open(task_id, worktree, socket, store, env, adapter_factory, socket_glob, claude, prompt, isolate) -> OpenResult:
    wt = Path(worktree or os.getcwd()).expanduser()
    if not wt.is_dir():
        return _not_opened(f"worktree {str(wt)!r} is not a directory")
    bad = check_install()
    if bad:
        return _not_opened(bad)
    claude = claude or shutil.which("claude", path=env.get("PATH"))
    if not claude:
        return _not_opened("claude not found on PATH; a tab would start with nothing to run")

    preferred = normalize_socket(socket or env["KITTY_LISTEN_ON"]) if (socket or env.get("KITTY_LISTEN_ON")) else None
    candidates = [socket] if socket else ([env["KITTY_LISTEN_ON"]] if env.get("KITTY_LISTEN_ON") else [])
    if not socket:
        candidates += sorted(glob.glob(socket_glob))
    live: dict[str, KittyAdapter] = {}
    windows = {}
    problem = None  # why the last candidate was unusable, for the not_opened reason
    for c in dict.fromkeys(normalize_socket(c) for c in candidates):
        try:
            ad = adapter_factory(c)
            windows[c] = ad.ls()
            live[c] = ad
        except KittyVersionMismatch as e:
            problem = f"unsupported kitty: {e}"
        except KittyError as e:
            problem = problem or f"{c}: {e}"
    if not live:
        if not candidates:
            return _not_opened("no kitty socket: not inside kitty and no /tmp/kitty-* found")
        return _not_opened(problem or "kitty is not running")

    for sock, wins in windows.items():
        for w in wins:
            if w.user_vars.get("task_id") == task_id:
                return OpenResult("exists", w.id, sock, "a live tab already carries this task")

    st = Store(store) if store else Store()
    prior = st.latest_for_task(task_id)
    if prior:
        fresh = "" if st.latest_session_for_task(task_id) else " (--fresh: no session was recorded)"
        return _not_opened(f"task has had a tab before and none is live now; run "
                           f"`resume {task_id}`{fresh} instead")

    if preferred in live:
        target = preferred
    elif len(live) == 1:
        target = next(iter(live))
    else:
        return _not_opened(f"{len(live)} kitty instances are running and none was chosen; "
                           "pass --socket")
    try:
        wt, isolated, why = _choose_directory(task_id, wt, isolate, st, windows)
    except worktrees.WorktreeError as e:  # never launch into a shared tree after a failure
        return _not_opened(f"worktree: {e}")
    mcp, mcp_source = mcp_choice(env)
    lau = Launcher(live[target], st, claude=claude, mcp_config=mcp, mcp_source=mcp_source)
    wid = lau.start(task_id, str(wt), prompt)
    return OpenResult("opened", wid, target, why, str(wt), isolated)


def _live_sibling(st: Store, windows: dict, repo: Path) -> str | None:
    """task_id of a live tab in `repo`, else None. Live = store row not closed AND its window is
    still in kitty's ls. Repo root is the main checkout, so a tab already in a worktree counts."""
    present = {(sock, w.id) for sock, wins in windows.items() for w in wins}
    for row in st.list(open_only=True):
        if (row.socket, row.window_id) in present and worktrees.repo_root(row.cwd) == repo:
            return row.task_id
    return None


def _choose_directory(task_id, wt, isolate, st, windows) -> tuple[Path, bool, str | None]:
    """(directory, isolated, reason). isolate=None means decide from live sibling tabs."""
    repo = worktrees.repo_root(wt)
    if repo is None:
        why = "not in a git repository; used the directory as given" if isolate else None
        return wt, False, why
    sibling = None
    if isolate is None:
        sibling = _live_sibling(st, windows, repo)
        isolate = sibling is not None
    if not isolate:
        return wt, False, None
    why = (f"isolated: task {sibling} has a live tab in {repo}" if sibling
           else "isolated: requested")
    return worktrees.ensure(repo, task_id), True, why
