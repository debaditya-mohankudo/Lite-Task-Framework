"""Backfill commit links from git history.

There is no automatic capture. A commit gets linked to a task by an explicit
tasks__add_commit call — the commit skill (.claude/skills/commit/skill.md)
makes that call after a successful commit — but nothing enforces it happens.
A commit made outside the skill, or one where the call was skipped, forgotten,
or failed, leaves no link, and the gap is invisible: a task with no commits
looks exactly like a task whose commits were never made.

This makes that recoverable. Git history is the durable record; the commit map
is a cache over it, and this rebuilds the cache from scratch by re-deriving
every task:<id> reference straight from git log. Nothing here depends on the
skill having run, or on anything having called tasks__add_commit at all.

    python -m taskfw.backfill [--repo PATH] [--since REV] [--dry-run]
"""
from __future__ import annotations

import argparse
import re
import sys

from taskfw.gitutil import run_git
from taskfw.log import get_logger
from taskfw.store import TaskStore

log = get_logger(__name__)

#: Matches task:<id> anywhere in a commit message.
TASK_REF = re.compile(r"\btask:([0-9a-f]{6,32})\b", re.IGNORECASE)

#: Unit separator between sha and subject — safe against both in commit text.
_SEP = "\x1f"


def extract_task_ids(text: str) -> list[str]:
    """Every task:<id> reference in a string, de-duplicated, order preserved."""
    seen: dict[str, None] = {}
    for match in TASK_REF.finditer(text or ""):
        seen.setdefault(match.group(1).lower(), None)
    return list(seen)


def git_log(repo: str, since: str | None = None) -> list[tuple[str, str]]:
    """Return [(sha, message), ...] newest first. Empty list if repo is not git."""
    rev = f"{since}..HEAD" if since else "HEAD"
    out = run_git(["log", rev, f"--format=%H{_SEP}%B%x00"], cwd=repo)
    if out is None:
        return []
    if out.returncode != 0:
        log.warning("backfill: not a git repo or bad rev: %s", repo)
        return []

    entries: list[tuple[str, str]] = []
    for chunk in out.stdout.split("\x00"):
        chunk = chunk.strip()
        if not chunk or _SEP not in chunk:
            continue
        sha, message = chunk.split(_SEP, 1)
        entries.append((sha.strip(), message))
    return entries


def backfill(repo: str, since: str | None = None, dry_run: bool = False,
             store: TaskStore | None = None) -> dict:
    """Link every commit whose message references a task that exists.

    Idempotent — add_commit is a no-op for an already-recorded (task, sha), so
    running this repeatedly is safe and cheap.
    """
    s = store or TaskStore()
    scanned = linked = skipped_unknown = already = 0

    for sha, message in git_log(repo, since):
        scanned += 1
        for task_id in extract_task_ids(message):
            if s.get(task_id) is None:
                skipped_unknown += 1
                continue
            if dry_run:
                linked += 1
                continue
            if s.add_commit(task_id, sha, repo):
                linked += 1
            else:
                already += 1

    result = {"scanned": scanned, "linked": linked, "already_linked": already,
              "unknown_tasks": skipped_unknown, "dry_run": dry_run}
    log.info("backfill %s: %s", repo, result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--repo", default=".", help="Repository to scan (default: cwd)")
    parser.add_argument("--since", default=None, help="Only commits after this rev")
    parser.add_argument("--dry-run", action="store_true", help="Report without writing")
    args = parser.parse_args(argv)

    result = backfill(args.repo, args.since, args.dry_run)
    print(
        f"scanned {result['scanned']} commits — "
        f"{result['linked']} linked, {result['already_linked']} already linked, "
        f"{result['unknown_tasks']} references to unknown tasks"
        + (" (dry run)" if args.dry_run else "")
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
