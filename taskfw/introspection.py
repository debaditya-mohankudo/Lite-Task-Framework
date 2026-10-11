"""The introspection report shape — a typed write side, mirroring taskfw.grooming.

The report used to be a bare ``dict``: any key accepted, an empty ``{}`` stored
and counted as "introspected", and a retried call appended a second copy. The
shape is now a type, so a malformed report is refused at the tool boundary
(the MCP layer renders ``additionalProperties: false``) instead of detected
afterwards.

EVERY FIELD IS OPTIONAL. The live store (290 reports over 276 tasks when this
was written) has no key that all reports share, and no reader — the lesson
nudge, accuracy's tallies — needs any key to be present. A required field
would be stricter than real use. The one floor is ``has_content``: a report
must say something.

Stored reports are not rewritten or folded on read (unlike grooming): reports
are append-only history, and ``surprises`` — the pre-canonical lesson shape —
is still read by two readers, so it stays in the schema. About 30 other
legacy keys exist in older reports; new writes put such thoughts in ``other``.

Stdlib + typing_extensions + pydantic config, so ``dispatcher.phase`` can use
``has_content`` without a cycle.
"""
from __future__ import annotations

from typing import Any

from pydantic import ConfigDict
from typing_extensions import TypedDict


class IntrospectionReport(TypedDict, total=False):
    """One introspection pass. Every key is optional; none is extra."""
    __pydantic_config__ = ConfigDict(extra="forbid")  # type: ignore[misc]
    date: str
    #: Self-reported tally ({"predicted": N, "materialized": M, ...}); a
    #: summary only — tasks__grooming_accuracy recomputes from the grades.
    grooming_accuracy: dict[str, Any] | None
    #: Entries are free text, or (rarely, in older reports) a dict.
    missed_surprises: list[str | dict[str, Any]]
    new_knowledge: list[str]
    stale_knowledge_flagged: list[str]
    highest_leverage: str
    overall_assessment: str
    #: Legacy lesson shape, still read by nudges and accuracy.
    surprises: list[dict[str, Any]]
    #: Anything the fields above do not anticipate, as free text.
    other: list[str]


def has_content(report: dict | None) -> bool:
    """Whether the report carries at least one non-empty value.

    ``{}`` and ``{"new_knowledge": []}`` say nothing, so neither counts as an
    introspection pass.
    """
    return any((report or {}).values())
