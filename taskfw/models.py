"""The task object — one typed structure, serialised as JSON.

A task is a single typed object rather than a markdown body plus a structured
sidecar. That choice removes two things outright rather than relocating them:
required-section validation becomes schema validation, and "resolution must be
a checklist" becomes unrepresentable, because resolution IS a list of items.

`notes` is deliberate and should survive later tidying. Over-structuring is how
a task template earns a reputation as a gauntlet; there has to be somewhere to
put a thought the schema did not anticipate.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

#: Two issue types only. 'epic' groups; 'task' does the work. No story / bug /
#: subtask, so there is no hierarchy matrix to enforce — see taskfw.lifecycle.
TASK_TYPES = ("epic", "task")

STATUSES = ("open", "blocked", "done", "abandoned")


def new_id() -> str:
    return uuid.uuid4().hex[:8]


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class ResolutionItem:
    text: str
    done: bool = False


@dataclass
class Link:
    to: str
    rel: str = "relates_to"


@dataclass
class Task:
    id: str = field(default_factory=new_id)
    type: str = "task"
    status: str = "open"
    parent: str | None = None
    title: str = ""
    motivation: str = ""
    resolution: list[ResolutionItem] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    notes: str = ""
    #: Findings from a grooming pass. Replaced wholesale each pass by design.
    grooming: dict = field(default_factory=dict)
    #: Introspection reports, appended — history is kept here, unlike grooming.
    introspection: list[dict] = field(default_factory=list)
    created_at: str = field(default_factory=utcnow)
    updated_at: str = field(default_factory=utcnow)

    # -- serialisation ------------------------------------------------------

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)

    @classmethod
    def from_dict(cls, data: dict) -> "Task":
        """Build a Task from a plain dict, tolerating unknown and missing keys.

        Unknown keys are dropped rather than raising: a store written by a newer
        version must stay readable by an older one, which matters because the
        migration path is additive-only and never rewrites rows.
        """
        known = set(cls.__dataclass_fields__)
        clean = {k: v for k, v in (data or {}).items() if k in known}
        clean["resolution"] = [
            r if isinstance(r, ResolutionItem) else ResolutionItem(**r)
            for r in clean.get("resolution") or []
        ]
        return cls(**clean)

    @classmethod
    def from_json(cls, raw: str | None) -> "Task":
        return cls.from_dict(json.loads(raw) if raw else {})

    # -- derived ------------------------------------------------------------

    @property
    def progress(self) -> tuple[int, int]:
        """(done, total) resolution items — derived, never stored.

        Storing it would create a counter that can drift from the list it
        counts; computing it cannot.
        """
        return sum(1 for r in self.resolution if r.done), len(self.resolution)

    def search_text(self) -> str:
        """Flattened text for the full-text index."""
        parts = [self.title, self.motivation, self.notes, " ".join(self.tags), " ".join(self.files)]
        parts += [r.text for r in self.resolution]
        return "\n".join(p for p in parts if p)
