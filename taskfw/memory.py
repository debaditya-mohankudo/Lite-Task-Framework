"""Loop memory — the lessons introspection produces that nothing else can hold.

SCOPED TO THE LOOP, and the scoping is the whole design. This is not a general
knowledge store and must not become one; a memory earns a place here only if
introspection produces it and no other part of the framework can hold it.

Doc 05 says a useful introspection pass produces one of four things. Three
already have homes: a graded risk lives in the task's grooming, a decision
lives in the task's events, and architectural knowledge lives in the concept
store. The remaining two — a constraint discovered the hard way, and a
technique worth reaching for again — belong to no single task and no single
module, and before this they had nowhere to go but prose that evaporated.

EVERY MEMORY CITES A TASK. Recording one requires the task it was learned from,
because a lesson with no evidence is an opinion, and the framework already
treats unevidenced claims as the thing to avoid. That citation is also what
makes the memory checkable later.

STANDING IS DERIVED, NEVER STORED. A memory is a prediction that a lesson
generalises, so later tasks grade it exactly as introspection grades a groomed
risk: `confirmed_by` when it held again, `contradicted_by` when it did not.
Confidence is computed from those links on every read. A stored confidence
number would be a self-assessment that drifts from its own evidence — the same
failure taskfw.accuracy exists to prevent.

SUPERSEDED, NOT DELETED. Doc 05 is explicit that obsolete knowledge is flagged
rather than removed unilaterally, so a replaced memory keeps its row and gains
a pointer to what replaced it. It leaves recall by default and stays reachable
on request; `forget` exists for entries that were simply wrong, which is a
different thing from outdated.

PULLED, NEVER PUSHED. Nothing here is injected. A memory reaches an agent only
because it asked — either by calling `task_memory__recall`, or by pulling a
full `tasks__context` bundle, whose `lessons` section is built from this store
(taskfw/context.py:lessons_for). Both are pulls, which is the same bargain the
rest of the framework makes.

That second path exists because the first one alone made this a diary. Doc 05
says to read lessons back when grooming, but if the only way to do it is a tool
the agent has to independently remember, then a lesson is optional reading and
the confirmed_by/contradicted_by edge rarely fires — a memory nobody recalls is
a memory nobody ever tests. Note the asymmetry it introduces: recall counts a
hit, the context bundle does not. See recall's `count_hit`.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from taskfw.config import DEFAULT_RECALL_LIMIT
from taskfw.db.connect import connect, transaction
from taskfw.log import get_logger
from taskfw.models import utcnow

log = get_logger(__name__)

#: What introspection actually produces. Deliberately short — a taxonomy longer
#: than this is a decision to make on every capture, and doc 05 names only two
#: of these outright. `pitfall` earns its place from the accuracy signals:
#: a recurring `wrong` or `missed` grade is a lesson about the loop itself.
KINDS = ("constraint", "technique", "pitfall")

RELATIONS = ("learned_from", "confirmed_by", "contradicted_by")

#: A memory whose text is shorter than this is a label, not a lesson.
MIN_TEXT = 20

_FTS_DDL = "CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(slug UNINDEXED, text)"

_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


@dataclass(frozen=True)
class Rejected(Exception):
    """A write the store structurally cannot accept.

    Distinct from a lifecycle refusal: these are shape errors, not policy. The
    store still enforces no policy — it rejects only what it cannot represent.
    """
    reason: str

    def __str__(self) -> str:
        return self.reason


class MemoryStore:
    """Data access for loop memories. Contains no policy beyond shape."""

    def __init__(self, path: Path | str | None = None, conn: sqlite3.Connection | None = None):
        self.conn = conn if conn is not None else connect(path)
        self.fts = self._try_enable_fts()

    def _try_enable_fts(self) -> bool:
        try:
            self.conn.execute(_FTS_DDL)
            self.conn.commit()
            return True
        except sqlite3.OperationalError as exc:
            log.warning("FTS5 unavailable for memories, recall falls back to LIKE (%s)", exc)
            return False

    def close(self) -> None:
        self.conn.close()

    # -- writes -------------------------------------------------------------

    def record(self, slug: str, text: str, task_id: str, kind: str = "constraint") -> dict:
        """Create or update a memory, citing the task it was learned from.

        `task_id` is required rather than optional. An optional citation is one
        nobody supplies, and an uncited lesson cannot be checked against the
        work that produced it.
        """
        if not _SLUG.match(slug or ""):
            raise Rejected(f"slug {slug!r} must be kebab-case: lowercase words joined by hyphens.")
        if kind not in KINDS:
            raise Rejected(f"Unknown kind {kind!r}. Valid: {', '.join(KINDS)}.")
        if len(text.strip()) < MIN_TEXT:
            raise Rejected(
                f"Text is {len(text.strip())} characters; a memory needs at least {MIN_TEXT}. "
                "State the lesson in a sentence, not a label."
            )

        existing = self.get(slug, include_superseded=True)
        with transaction(self.conn):
            self.conn.execute(
                """INSERT INTO memories (slug, kind, text, updated_at) VALUES (?,?,?,?)
                   ON CONFLICT(slug) DO UPDATE SET
                     kind=excluded.kind, text=excluded.text, updated_at=excluded.updated_at""",
                (slug, kind, text.strip(), utcnow()),
            )
            self.conn.execute(
                """INSERT OR IGNORE INTO memory_links (slug, task_id, relation)
                   VALUES (?,?,'learned_from')""",
                (slug, task_id),
            )
            if self.fts:
                self.conn.execute("DELETE FROM memories_fts WHERE slug=?", (slug,))
                self.conn.execute(
                    "INSERT INTO memories_fts (slug, text) VALUES (?,?)", (slug, f"{slug} {text}")
                )
        log.info("memory %s: %s kind=%s from task=%s",
                 slug, "updated" if existing else "recorded", kind, task_id)
        return self.get(slug, include_superseded=True)

    def link(self, slug: str, task_id: str, relation: str) -> bool:
        """Record that a task confirmed or contradicted a memory. Idempotent."""
        if relation not in RELATIONS:
            raise Rejected(f"Unknown relation {relation!r}. Valid: {', '.join(RELATIONS)}.")
        if self.get(slug, include_superseded=True) is None:
            raise Rejected(f"No memory {slug!r}.")
        with transaction(self.conn):
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO memory_links (slug, task_id, relation) VALUES (?,?,?)",
                (slug, task_id, relation),
            )
        created = cur.rowcount > 0
        log.info("memory %s: %s by task=%s %s", slug, relation, task_id,
                 "recorded" if created else "already recorded")
        return created

    def supersede(self, slug: str, by: str) -> dict:
        """Mark a memory obsolete, pointing at what replaced it.

        The row survives. Doc 05 forbids removing obsolete knowledge
        unilaterally, and a lesson that stopped being true is itself evidence
        about how the system changed.
        """
        for s in (slug, by):
            if self.get(s, include_superseded=True) is None:
                raise Rejected(f"No memory {s!r}.")
        if slug == by:
            raise Rejected("A memory cannot supersede itself.")
        with transaction(self.conn):
            self.conn.execute(
                "UPDATE memories SET superseded_by=?, updated_at=? WHERE slug=?",
                (by, utcnow(), slug),
            )
        log.info("memory %s: superseded by %s", slug, by)
        return self.get(slug, include_superseded=True)

    def forget(self, slug: str) -> bool:
        """Delete a memory outright. For entries that were WRONG, not outdated.

        Outdated knowledge is superseded, which keeps the trail. This is the
        escape hatch for a lesson that should never have been recorded, and it
        cascades to the links so no orphaned standing survives.
        """
        with transaction(self.conn):
            cur = self.conn.execute("DELETE FROM memories WHERE slug=?", (slug,))
            if self.fts:
                self.conn.execute("DELETE FROM memories_fts WHERE slug=?", (slug,))
        deleted = cur.rowcount > 0
        log.info("memory %s: %s", slug, "forgotten" if deleted else "not found, nothing deleted")
        return deleted

    # -- reads --------------------------------------------------------------

    def get(self, slug: str, include_superseded: bool = True) -> dict | None:
        row = self.conn.execute("SELECT * FROM memories WHERE slug=?", (slug,)).fetchone()
        if row is None:
            return None
        memory = self._hydrate(row)
        if memory["superseded_by"] and not include_superseded:
            return None
        return memory

    def recall(self, query: str = "", kind: str = "", limit: int = DEFAULT_RECALL_LIMIT,
               include_superseded: bool = False, count_hit: bool = True) -> list[dict]:
        """Search memories. Superseded ones are excluded unless asked for.

        Excluding by default is the point of superseding: stale knowledge that
        keeps surfacing is worse than none, because it reads as current.

        `count_hit` exists because this method has a write side effect, and one
        caller must not have it. hit_count/last_hit are meant to answer "which
        lessons does anyone actually reach for" — a question about deliberate
        recall. tasks__context calls recall while assembling a bundle nobody
        asked lessons for specifically, so counting those would make the
        counters measure bundle assembly instead, and there would be no way
        afterwards to separate the two. Passing False deletes that failure
        mode rather than leaving a contaminated number to be reconciled later.
        """
        slugs = self._matching_slugs(query, limit) if query.strip() else None
        sql = "SELECT * FROM memories"
        where, params = [], []
        if slugs is not None:
            if not slugs:
                return []
            where.append(f"slug IN ({','.join('?' * len(slugs))})")
            params.extend(slugs)
        if kind:
            where.append("kind=?")
            params.append(kind)
        if not include_superseded:
            where.append("superseded_by=''")
        if where:
            sql += " WHERE " + " AND ".join(where)
        if slugs is None:
            sql += " ORDER BY updated_at DESC"
        sql += " LIMIT ?"
        params.append(limit)
        rows = [self._hydrate(r) for r in self.conn.execute(sql, params)]
        if slugs is not None:
            # _matching_slugs is already ranked by bm25 (best match first);
            # the IN (...) clause above does not preserve that order, so
            # re-sort by it here rather than falling back to recency.
            rank = {slug: i for i, slug in enumerate(slugs)}
            rows.sort(key=lambda m: rank.get(m["slug"], len(slugs)))
        if rows and count_hit:
            hit_at = utcnow()
            with transaction(self.conn):
                self.conn.executemany(
                    "UPDATE memories SET hit_count = hit_count + 1, last_hit = ? WHERE slug = ?",
                    [(hit_at, m["slug"]) for m in rows],
                )
            for m in rows:
                m["hit_count"] += 1
                m["last_hit"] = hit_at
        log.info(
            "memory recall query=%r kind=%r fetched=%d counted=%s slugs=%s",
            query, kind, len(rows), count_hit, [r["slug"] for r in rows],
        )
        return rows

    def _matching_slugs(self, query: str, limit: int) -> list[str]:
        if self.fts:
            try:
                rows = self.conn.execute(
                    "SELECT slug FROM memories_fts WHERE memories_fts MATCH ? "
                    "ORDER BY bm25(memories_fts) LIMIT ?",
                    (self._fts_or_query(query), limit * 4),
                ).fetchall()
                return [r["slug"] for r in rows]
            except sqlite3.OperationalError as exc:
                # A malformed FTS5 query is user input, not a reason to claim
                # there are no matching memories.
                log.warning("memory FTS query failed, falling back to LIKE (%s)", exc)
        like = f"%{query}%"
        rows = self.conn.execute(
            "SELECT slug FROM memories WHERE slug LIKE ? OR text LIKE ? LIMIT ?",
            (like, like, limit * 4),
        ).fetchall()
        return [r["slug"] for r in rows]

    @staticmethod
    def _fts_or_query(query: str) -> str:
        """OR the query's terms instead of FTS5's default implicit AND.

        A paraphrased multi-word query should surface the best partial match,
        not require every word to appear verbatim. Each term is quoted so a
        hyphen or stray quote in user input is treated as literal text, never
        as an FTS5 operator.
        """
        terms = query.split()
        quoted = ['"{}"'.format(t.replace('"', '""')) for t in terms]
        return " OR ".join(quoted)

    def _hydrate(self, row: sqlite3.Row) -> dict:
        """Attach the derived standing. Never reads a stored confidence."""
        links = self.conn.execute(
            "SELECT task_id, relation FROM memory_links WHERE slug=? ORDER BY created_at",
            (row["slug"],),
        ).fetchall()
        by_relation: dict[str, list[str]] = {r: [] for r in RELATIONS}
        for link in links:
            by_relation.setdefault(link["relation"], []).append(link["task_id"])

        confirmed = len(by_relation["confirmed_by"])
        contradicted = len(by_relation["contradicted_by"])
        return {
            "slug": row["slug"],
            "kind": row["kind"],
            "text": row["text"],
            "superseded_by": row["superseded_by"],
            "learned_from": by_relation["learned_from"],
            "confirmed_by": by_relation["confirmed_by"],
            "contradicted_by": by_relation["contradicted_by"],
            "standing": _standing(confirmed, contradicted, bool(row["superseded_by"])),
            "hit_count": row["hit_count"],
            "last_hit": row["last_hit"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }


def _standing(confirmed: int, contradicted: int, superseded: bool) -> str:
    """One word for how much weight a memory currently carries.

    Derived on every read. `disputed` is deliberately reported rather than
    resolved: a contradicted memory returned as though it were reliable is an
    omission indistinguishable from an absence, and the caller is the one who
    can tell which side is right.
    """
    if superseded:
        return "superseded"
    if contradicted and confirmed:
        return "disputed"
    if contradicted:
        return "contradicted"
    if confirmed:
        return "confirmed"
    return "unverified"
