"""Risk identity — the one shape rule every risk consumer shares.

A grooming risk is stored as a plain dict inside ``Task.grooming['risks']`` and
replaced wholesale each grooming pass, EXCEPT that a graded verdict is merged
forward by identity (see ``mcp_server._merge_grooming_risks``). Four code paths
need the same two primitives to do that:

* turn a raw risk — a bare string, a dict, or something malformed — into a
  canonical dict, and
* collapse a risk's text to a comparable key.

Before this module those primitives were re-derived in
``mcp_server._coerce_risk``, ``mcp_server._merge_grooming_risks``,
``accuracy._risks``, and ``accuracy._RecurrenceGrouper`` — and ``mcp_server``
reached into ``accuracy._normalise``, a portable tool module importing an
analytics module's private. That is the "every rule lives in exactly one
place" rule applied to risk identity.

WHAT DOES NOT LIVE HERE. Identity itself (id-if-present-else-normalised-text,
id-less risks kept in their own keyspace) and the two distinct merge/group
algorithms stay where they are — ``_merge_grooming_risks`` assigns ids to
incoming id-less risks and text-matches them, while ``_RecurrenceGrouper``
keeps id-less risks apart and never merges them with id-bearing ones. Those
are genuinely different operations; only the shared primitives are here.

Stdlib only — no ``taskfw`` imports — so every consumer can depend on this
without a cycle.
"""
from __future__ import annotations

import re
from typing import Any

#: The documented risk shape. A consumer may also be handed a bare string (a
#: plausible thing to write by hand) or, defensively, something malformed.
RISK_KEYS = ("id", "text", "graded")


def normalise_text(text: str) -> str:
    """Collapse a risk's text to a comparable key.

    Runs of whitespace, surrounding whitespace, case, and a single trailing
    period are not signal — two risks that differ only in those are the same
    prediction for identity purposes.
    """
    return re.sub(r"\s+", " ", (text or "").strip().lower()).rstrip(".")


def coerce(raw: Any) -> dict[str, Any]:
    """A raw grooming risk as a dict, tolerating a bare string or worse.

    A dict is copied as-is: every key is preserved and NO key is manufactured.
    The *absence* of ``id`` is load-bearing to the merge — a risk with no id is
    a brand-new prediction the merge assigns an id to — so this never adds one.

    A bare string becomes ``{"text": raw, "graded": None}``. Anything else is
    coerced through ``str()`` the same way, so a malformed entry still carries
    text rather than vanishing: an ungraded risk that disappeared would be an
    omission indistinguishable from an absence, the one failure the accuracy
    module exists to prevent.
    """
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        return {"text": raw, "graded": None}
    return {"text": str(raw), "graded": None}
