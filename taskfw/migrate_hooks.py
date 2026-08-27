"""One-time import of claude-hooks' task store into taskfw.

claude-hooks keeps its tasks in ~/.claude/proj_tasks.db and is giving up task
functionality entirely, so everything moves here — no project filter, because a
filter would strand whatever it excluded in a database nothing reads.

WRITES THROUGH TaskStore.save(), never raw SQL. `tasks.data` holds the typed
object and the scalar columns are projected from it; save() is the sole writer
of both, and an INSERT here would be a second writer whose rows disagree with
themselves the moment either side changes.

Re-runnable. save() upserts by id and the side tables use INSERT OR IGNORE
against their natural keys, so a second run changes nothing.

The mapping is lossy in three places, each decided rather than stumbled into
(task:d6ddb40f):

  * Five issue types become one. epic stays epic; task, story, bug, subtask and
    feedback all become `task`, tagged with the type they arrived as. Those four
    carried no rule taskfw enforces — they were labels, and a tag is where a
    label belongs.
  * `review` is not a taskfw status. Those rows arrive `open`, tagged
    `was-review`. It is a non-terminal working state, and the alternative
    non-terminal status, blocked, would assert an obstacle that may not exist.
  * An epic with a parent cannot exist here, so it becomes a task tagged
    `was-epic`. Demoting discards a label; dropping the parent would discard a
    real relationship.

Session identity is dropped outright. Source events carry prompt_id, session_id
and turn; this framework has no session concept, and inventing columns to hold
three fields nothing would ever query is how a schema starts rotting.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path

from taskfw.db import connect
from taskfw.log import get_logger
from taskfw.task import TASK_STATUSES, ResolutionItem, Task
from taskfw.store import TaskStore

log = get_logger(__name__)

#: Where claude-hooks keeps its tasks (src/tools/tasks.py:31).
SOURCE_DEFAULT = Path.home() / ".claude" / "proj_tasks.db"

#: Markdown checklist item, ticked or not. claude-hooks tracked resolution as
#: prose in `body`; taskfw has a typed list, so the items are recovered rather
#: than left buried in text where nothing can count them.
CHECKLIST = re.compile(r"^\s*[-*]\s+\[([ xX])\]\s+(.+?)\s*$", re.MULTILINE)


@dataclass
class Report:
    """What actually happened, counted rather than asserted."""
    tasks: int = 0
    epics_demoted: int = 0
    review_remapped: int = 0
    status_unknown: int = 0
    events: int = 0
    events_empty: int = 0
    edges: int = 0
    commits: int = 0
    #: Rows whose task_id (or edge endpoint) names a task absent from
    #: open_tasks. Counted rather than skipped quietly: a dropped row that
    #: nothing reports is indistinguishable from a row that never existed,
    #: and the caller has no other way to find out.
    events_orphaned: int = 0
    edges_orphaned: int = 0
    commits_orphaned: int = 0
    unplaceable: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [
            f"tasks migrated      {self.tasks}",
            f"  epics demoted     {self.epics_demoted}  (had a parent)",
            f"  review -> open    {self.review_remapped}",
            f"  unknown status    {self.status_unknown}",
            f"events migrated     {self.events}",
            f"  skipped (empty)   {self.events_empty}",
            f"  orphaned          {self.events_orphaned}  (task_id not in open_tasks)",
            f"edges migrated      {self.edges}",
            f"  orphaned          {self.edges_orphaned}  (endpoint not in open_tasks)",
            f"commits migrated    {self.commits}",
            f"  orphaned          {self.commits_orphaned}  (task_id not in open_tasks)",
        ]
        if self.unplaceable:
            lines.append(
                f"UNPLACEABLE         {len(self.unplaceable)} — parent cycle or "
                f"missing parent: {', '.join(self.unplaceable[:10])}"
            )
        return "\n".join(lines)


def _tags(raw: str | None) -> list[str]:
    seen: list[str] = []
    for tag in (raw or "").split(","):
        tag = tag.strip()
        if tag and tag not in seen:
            seen.append(tag)
    return seen


def _resolution(body: str | None) -> list[ResolutionItem]:
    return [
        ResolutionItem(text=text, done=mark.lower() == "x")
        for mark, text in CHECKLIST.findall(body or "")
    ]


def _document(raw: str | None) -> tuple[dict, list[dict]]:
    """Pull grooming and introspection out of claude-hooks' `document` blob.

    The field names match taskfw's own grooming dict, so this is a move rather
    than a translation. A blob that will not parse is skipped, not guessed at.
    """
    if not raw or raw.strip() in ("", "{}"):
        return {}, []
    try:
        doc = json.loads(raw)
    except (ValueError, TypeError):
        log.warning("unparseable document blob — skipped")
        return {}, []
    grooming = doc.get("grooming") or {}
    if isinstance(grooming, dict):
        grooming = {k: v for k, v in grooming.items() if v}
    else:
        grooming = {}
    reports = (doc.get("introspection") or {}).get("reports") or []
    return grooming, [r for r in reports if isinstance(r, dict)]


def to_task(row: sqlite3.Row, report: Report) -> Task:
    issue_type = (row["issue_type"] or "task").strip().lower()
    parent = (row["parent_id"] or "").strip() or None
    tags = _tags(row["tags"])

    task_type = "epic" if issue_type == "epic" else "task"
    if task_type == "epic" and parent:
        task_type = "task"
        report.epics_demoted += 1
        if "was-epic" not in tags:
            tags.append("was-epic")
    if issue_type not in ("epic", "task") and issue_type not in tags:
        tags.append(issue_type)

    status = (row["status"] or "open").strip().lower()
    if status == "review":
        status = "open"
        report.review_remapped += 1
        if "was-review" not in tags:
            tags.append("was-review")
    elif status not in TASK_STATUSES:
        report.status_unknown += 1
        if f"was-{status}" not in tags:
            tags.append(f"was-{status}")
        status = "open"

    grooming, introspection = _document(row["document"])

    return Task(
        id=row["id"],
        type=task_type,
        status=status,
        parent=parent,
        title=row["title"] or "",
        # `body` is free-form templated prose. It goes to notes verbatim rather
        # than being split into motivation — guessing which paragraph was the
        # motivation would fabricate structure the source never had.
        notes=row["body"] or "",
        resolution=_resolution(row["body"]),
        tags=tags,
        grooming=grooming,
        introspection=introspection,
        created_at=str(row["created_at"] or ""),
        updated_at=str(row["updated_at"] or ""),
    )


def _ordered(rows: list[sqlite3.Row]) -> tuple[list[sqlite3.Row], list[str]]:
    """Parents before children, so no row is saved referencing a missing parent.

    Returns (ordered, unplaceable). Anything left over is in a parent cycle or
    points at a row not present — reported, never silently reparented.
    """
    remaining = {r["id"]: r for r in rows}
    placed: set[str] = set()
    ordered: list[sqlite3.Row] = []

    while remaining:
        ready = [
            r for r in remaining.values()
            if not (r["parent_id"] or "").strip()
            or (r["parent_id"] or "").strip() in placed
        ]
        if not ready:
            break
        for r in ready:
            ordered.append(r)
            placed.add(r["id"])
            del remaining[r["id"]]

    return ordered, sorted(remaining)


def migrate(source: Path, *, dry_run: bool = False, dest: Path | None = None) -> Report:
    """Import `source` into the taskfw database, or into `dest` when given.

    `dest` exists so this is testable against a scratch database. It defaults
    to the configured one, so the CLI needs no knowledge of it.
    """
    report = Report()

    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row

    rows = list(src.execute("SELECT * FROM open_tasks"))
    ordered, unplaceable = _ordered(rows)
    report.unplaceable = unplaceable

    conn = connect(dest)
    store = TaskStore(conn=conn)

    for row in ordered:
        task = to_task(row, report)
        if not dry_run:
            store.save(task)
        report.tasks += 1

    known = {r["id"] for r in ordered}

    for ev in src.execute("SELECT * FROM task_events"):
        if ev["task_id"] not in known:
            report.events_orphaned += 1
            continue
        text = (ev["summary"] or "").strip()
        if not text:
            report.events_empty += 1
            continue
        if not dry_run:
            # task_events has an autoincrement id and no natural key, so there
            # is nothing for INSERT OR IGNORE to collide with — a second run
            # duplicated all 4072 rows. Dedupe on the tuple that actually
            # identifies an imported event instead. The schema is additive-only
            # by design, so a UNIQUE constraint cannot be added after the fact;
            # the guard belongs here, at the only writer that needs it.
            conn.execute(
                "INSERT INTO task_events (task_id, ts, kind, text) "
                "SELECT ?, ?, ?, ? WHERE NOT EXISTS ("
                "  SELECT 1 FROM task_events"
                "  WHERE task_id=? AND ts=? AND kind=? AND text=?"
                ")",
                (ev["task_id"], str(ev["logged_at"] or ""), "hooks_event", text,
                 ev["task_id"], str(ev["logged_at"] or ""), "hooks_event", text),
            )
        report.events += 1

    for edge in src.execute("SELECT * FROM task_edges"):
        if edge["from_id"] not in known or edge["to_id"] not in known:
            report.edges_orphaned += 1
            continue
        if not dry_run:
            conn.execute(
                "INSERT OR IGNORE INTO task_edges (from_id, to_id, rel, created_at) "
                "VALUES (?, ?, ?, ?)",
                (edge["from_id"], edge["to_id"], edge["relation_type"] or "relates_to",
                 str(edge["created_at"] or "")),
            )
        report.edges += 1

    for c in src.execute("SELECT * FROM commit_task_map"):
        if c["task_id"] not in known:
            report.commits_orphaned += 1
            continue
        if not dry_run:
            conn.execute(
                "INSERT OR IGNORE INTO task_commits (task_id, sha, repo, ts) "
                "VALUES (?, ?, ?, ?)",
                (c["task_id"], c["commit_hash"], c["repo_path"] or "",
                 str(c["logged_at"] or "")),
            )
        report.commits += 1

    if not dry_run:
        conn.commit()
    src.close()
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--source", type=Path, default=SOURCE_DEFAULT,
                    help=f"claude-hooks task database (default: {SOURCE_DEFAULT})")
    ap.add_argument("--dry-run", action="store_true",
                    help="count everything, write nothing")
    args = ap.parse_args()

    if not args.source.exists():
        print(f"No such database: {args.source}", file=sys.stderr)
        return 1

    report = migrate(args.source, dry_run=args.dry_run)
    print(f"{'DRY RUN — nothing written' if args.dry_run else 'Migrated'} from {args.source}\n")
    print(report.render())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
