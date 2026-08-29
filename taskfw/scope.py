"""Scope — which project a task belongs to, derived once and spelled one way.

WHY THIS IS NOT A PATH. The obvious scope is the working directory, and the
store already computes one (`mcp_server._scope()`) and throws it away. It was
thrown away for a good reason that nobody wrote down: an absolute path is an
absolute path *on one machine*. It does not survive a move, a second checkout,
or a worktree, and it leaks a home directory into every row.

The evidence that free-text project identity fragments is already in this
database, in `task_commits.repo` — a caller-supplied column holding five
spellings of the same two projects (an absolute path, a bare name, a `~`-path,
an empty string). That is what happens when every writer decides for itself.
So this module is the only place a scope is produced, and both writers
(`Task.scope` and `task_commits.repo`) go through it — a second derivation
would just be a sixth convention.

THE SCHEME PREFIX IS LOAD-BEARING. A scope is `git:<host>/<path>` when it came
from a remote and `path:<abs>` when it did not. The prefix exists so two scopes
derived by different routes can never compare equal by accident, and so a
reader can always tell *how* a scope was known rather than guessing. A bare
string would make a fallback indistinguishable from a real answer, which is the
one failure mode this project refuses.

WHAT ORIGIN COSTS. A monorepo has one origin for many sub-projects, so this is
coarser than the working directory and will call two sibling projects the same
scope. That is a real loss, stated rather than papered over. It buys the
opposite case, which is more common here: worktrees and second clones of one
repo resolve to a single scope, which a path-based answer gets wrong today.

FAILING OPEN. Deriving a scope runs `git`. Every failure mode — not a repo, no
origin, git absent, git hung — returns the `path:` fallback rather than raising,
matching `backfill.git_log`'s established shape. A scope is a filter, and a
missing filter must degrade to "unscoped", never to a blocked write.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from taskfw.log import get_logger

log = get_logger(__name__)

#: Prefix for a scope derived from a git remote — portable across machines.
GIT = "git:"

#: Prefix for a scope that fell back to the working directory. Distinguishable
#: from GIT on purpose: see "THE SCHEME PREFIX IS LOAD-BEARING" above.
PATH = "path:"

#: Prefix for a caller-supplied repo string that could not be resolved to a
#: real directory, so no derivation was possible — see `for_repo`. Kept
#: verbatim after the prefix: unverified is not the same as wrong.
HINT = "hint:"

#: Seconds to wait on `git remote get-url`. Generous — this is a local read of
#: .git/config, so anything approaching this means git is wedged, not slow.
TIMEOUT = 10

#: scp-style remote: user@host:path. Not a URL, so urlparse cannot read it.
_SCP_RE = re.compile(r"^(?:[^@/]+@)?([^:/]+):(.+)$")

#: url-style remote: scheme://[user@]host[:port]/path
_URL_RE = re.compile(r"^[a-z][a-z0-9+.-]*://(?:[^@/]+@)?([^:/]+)(?::\d+)?/(.+)$", re.IGNORECASE)

#: Derivation is a subprocess and both writers call it on every write, while a
#: repository's origin does not change within a process's lifetime. Keyed by
#: the resolved directory, so two worktrees of one repo still each pay once.
_cache: dict[str, str] = {}


def normalise_remote(url: str) -> str | None:
    """`git:<host>/<path>` for a remote URL, or None if it is not one.

    Pure and git-free so the normalisation rule — the part that actually
    decides whether two clones agree — is testable without a repository.

    SSH and HTTPS spellings of one remote must land on the same string or the
    fragmentation this module exists to end just reappears under a new name.
    Both are reduced to host plus path, lowercased, with a `.git` suffix and
    surrounding slashes removed:

        git@github.com:Org/Repo.git      -> git:github.com/org/repo
        https://github.com/Org/Repo.git  -> git:github.com/org/repo
        ssh://git@github.com/Org/Repo    -> git:github.com/org/repo
    """
    url = (url or "").strip()
    if not url:
        return None
    match = _URL_RE.match(url) or _SCP_RE.match(url)
    if not match:
        return None
    host, path = match.group(1), match.group(2)
    path = path.strip("/")
    if path.lower().endswith(".git"):
        path = path[: -len(".git")]
    path = path.strip("/")
    if not host or not path:
        return None
    return f"{GIT}{host.lower()}/{path.lower()}"


def _origin_url(cwd: str) -> str | None:
    """`git remote get-url origin` in cwd, or None for any failure at all.

    Every failure is the same answer on purpose. A caller cannot act
    differently on "not a repo" than on "git is missing" — both mean no remote
    is knowable here — so distinguishing them would only add branches nobody
    can use. The reason still reaches the log.
    """
    try:
        out = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=cwd, capture_output=True, text=True, timeout=TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("scope: git remote failed in %s (%s)", cwd, exc)
        return None
    if out.returncode != 0:
        log.debug("scope: no origin in %s", cwd)
        return None
    return out.stdout.strip() or None


def derive(cwd: str | None = None) -> str:
    """The scope for a working directory. Never raises, never returns empty.

    Prefers the git origin; falls back to the resolved absolute path under the
    `path:` prefix. TASKFW_SCOPE overrides the *directory being asked about*,
    not the answer — an operator pointing it at another checkout still gets
    that checkout's origin, so the override cannot be used to smuggle in a
    sixth spelling of a project name.
    """
    cwd = cwd or os.environ.get("TASKFW_SCOPE") or os.getcwd()
    try:
        key = str(Path(cwd).resolve())
    except OSError:
        key = str(cwd)
    if key in _cache:
        return _cache[key]
    url = _origin_url(key)
    scope = (normalise_remote(url) if url else None) or f"{PATH}{key}"
    _cache[key] = scope
    log.debug("scope: %s -> %s", key, scope)
    return scope


def for_repo(repo: str = "") -> str:
    """The scope for a commit's `repo` hint — `task_commits.repo`'s one writer.

    `repo` arrives as free text from two very different callers: `backfill`
    hands over a real directory it is about to run `git log` in, while
    `tasks__add_commit` takes whatever a caller typed. Both end up in the same
    column, which is precisely how that column came to hold five spellings of
    two projects. Routing both through here gives them one vocabulary.

    Three outcomes, and the prefix always says which happened:

      empty               -> derive() on the working directory
      a real directory    -> derive() on that directory (`git:` or `path:`)
      anything else       -> `hint:<what the caller said>`, verbatim

    The third case is the interesting one. An unresolvable string — a bare
    repo name, a `~`-path from another machine — cannot be turned into a real
    scope, and quietly deriving the *server's* cwd instead would put a
    confident wrong answer where an honest unknown belongs. So the caller's
    text is kept exactly as given under a prefix that marks it unverified: it
    can never compare equal to a derived scope, and nothing is destroyed.
    """
    repo = (repo or "").strip()
    if not repo:
        return derive()
    try:
        expanded = Path(repo).expanduser()
        if expanded.is_dir():
            return derive(str(expanded))
    except OSError as exc:
        log.debug("scope: could not stat repo hint %r (%s)", repo, exc)
    return f"{HINT}{repo}"


def reset_cache() -> None:
    """Forget every derived scope. For tests, which build repos mid-process."""
    _cache.clear()
