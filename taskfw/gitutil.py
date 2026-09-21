"""The one place a module shells out to git.

Both taskfw.backfill (git log, to re-derive commit links) and taskfw.scope
(git remote, to derive a project's identity) need the same shape: run git,
treat a missing binary/timeout/permission error as "could not ask", and let
the caller decide what a non-zero exit means for its own case (backfill logs
"not a git repo or bad rev"; scope logs "no origin" at a quieter level,
since no-origin is the common case for a scope derivation, not a failure).
That process-level try/except was written out twice before this existed —
factored out here so a change to timeout handling or exception types happens
once. taskfw.mcp_server reads its loaded commit through head_sha here too.
"""
from __future__ import annotations

import subprocess

from taskfw.log import get_logger

log = get_logger(__name__)

#: Generous default — both call sites are local reads (.git/config, packed
#: refs), so anything approaching this means git is wedged, not slow.
DEFAULT_TIMEOUT = 30


def run_git(argv: list[str], cwd: str, timeout: int = DEFAULT_TIMEOUT) -> subprocess.CompletedProcess | None:
    """`git <argv>` in `cwd`. None for any process-level failure, never raises.

    Deliberately does NOT inspect `returncode` — a non-zero exit is often
    meaningful (not a repo, unknown revision, no such remote) and each
    caller wants to log and handle that itself rather than have it collapsed
    into the same None a missing git binary produces.
    """
    try:
        return subprocess.run(
            ["git", *argv], cwd=cwd, capture_output=True, text=True, timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("git %s failed in %s (%s)", " ".join(argv), cwd, exc)
        return None


def head_sha(cwd: str) -> str | None:
    """The commit HEAD names in `cwd`, or None when it cannot be read.

    None covers every way of not knowing — git missing, not a checkout, an
    unborn branch — so a caller can never mistake an empty string for a sha.
    """
    out = run_git(["rev-parse", "HEAD"], cwd=cwd)
    if out is None or out.returncode != 0:
        return None
    return out.stdout.strip() or None
