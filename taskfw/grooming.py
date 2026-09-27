"""The grooming shape — one typed structure, and the one fold that gets old data into it.

Grooming used to be a bare ``dict``. Any key was accepted, stored, and never
read: 48 tasks carried ``last_run_at``, others ``summary`` or
``prior_art_note``, and a findings-free blob still flipped ``is_groomed``
(task:0b9de163, review 2026-09-27 G2). The shape is now a type instead of a
convention, so a malformed key is rejected at the tool boundary rather than
detected afterwards.

TWO HALVES, ONE RULE.

* ``Grooming`` is the write shape. ``tasks__update`` takes it as a typed
  parameter; the MCP layer renders it with ``additionalProperties: false`` and
  refuses an unknown key before any taskfw code runs.
* ``normalise`` is the read shape. ``Task.__post_init__`` runs it on every
  Task, so a blob stored before the type existed arrives canonical. An
  off-schema key is not dropped: it is folded into ``other`` as
  ``"<key>: <value>"``. Dropping it on the next write would be losing data
  quietly, which the framework does not allow even for data nobody reads.

Because every Task in memory is already canonical, anything a caller reads
back (the ``tasks__context`` bundle, which introspection re-passes wholesale
when grading) contains only schema keys, so the strict write side never
rejects a legitimate round-trip.

``other`` is the escape hatch, and it is deliberate: where the schema does not
anticipate a thought, there has to be somewhere to put it.

Stdlib + typing_extensions + pydantic config only, and no ``taskfw`` imports,
so ``task.py`` can depend on it without a cycle.
"""
from __future__ import annotations

import json
from typing import Any

from pydantic import ConfigDict
from typing_extensions import NotRequired, TypedDict


class GroomingRisk(TypedDict):
    """A falsifiable prediction. ``id`` is framework-assigned — see
    ``mcp_server._merge_grooming_risks``; ``graded`` is set by introspection."""
    __pydantic_config__ = ConfigDict(extra="forbid")  # type: ignore[misc]
    text: str
    id: NotRequired[str]
    graded: NotRequired[str | None]


class OpenQuestion(TypedDict):
    __pydantic_config__ = ConfigDict(extra="forbid")  # type: ignore[misc]
    question: str
    blocking: NotRequired[bool]


class Grooming(TypedDict, total=False):
    """Findings from one grooming pass. Every key is optional; none is extra."""
    __pydantic_config__ = ConfigDict(extra="forbid")  # type: ignore[misc]
    clarifications: list[str]
    hidden_assumptions: list[str]
    open_questions: list[OpenQuestion]
    risks: list[GroomingRisk]
    prior_art: list[str]
    suggested_improvements: list[str]
    #: Anything the fields above do not anticipate, as free text.
    other: list[str]


#: The schema keys, in declaration order. Derived from the type so the two
#: cannot disagree.
GROOMING_FIELDS: tuple[str, ...] = tuple(Grooming.__annotations__)


def normalise(raw: dict | None) -> dict[str, Any]:
    """``raw`` with every off-schema key folded into ``other``.

    Schema keys pass through untouched, so this is the identity on anything
    the typed write side accepted. Only a stored blob from before the type
    existed changes, and nothing in it is lost.
    """
    if not raw:
        return {}
    out: dict[str, Any] = {}
    folded: list[str] = []
    for key, value in raw.items():
        if key in GROOMING_FIELDS:
            out[key] = value
        else:
            text = value if isinstance(value, str) else json.dumps(value, sort_keys=True)
            folded.append(f"{key}: {text}")
    if folded:
        out["other"] = list(out.get("other") or []) + folded
    return out
