"""Which claude model a kitty tab starts on: the one home for the ids and for the choice.

Ids live here and nowhere else, so the launcher,
opener and MCP tool cannot disagree on what a tier means. The choice is deterministic rules over what
the job is, not a model call: free, instant, and its reason string is recorded so introspection can
grade whether the tier was right. Choosing is a convenience: every failure ends as (None, why),
meaning "no --model flag, claude's own default", and never blocks a launch.

Precedence, applied by `pick` and nowhere else: the caller's model (the session that opened the tab and
has read the task; normally Opus) always wins, then these rules, then claude's own default. The rules are
only the fallback for a caller that passed nothing. The caller's one-line reason, not a rule string, is what
gets recorded as the source. Never drive `/model` into a tab: it rewrites the user's global default in
~/.claude/settings.json; `--model` is the only supported route.
"""
from __future__ import annotations

import re
from typing import Callable

SONNET_5 = "claude-sonnet-5"
SONNET_5_5 = "claude-sonnet-5-5"
OPUS_5 = "claude-opus-5"
OPUS_5_5 = "claude-opus-5-5"
TIERS = (SONNET_5, SONNET_5_5, OPUS_5, OPUS_5_5)  # cheapest to most capable

_HARD = re.compile(r"\b(design|architect\w*|refactor\w*|migrat\w*|schema|security|concurren\w*|"
                   r"race|invariant|protocol|redesign\w*|rewrite)\b", re.I)


def for_task(*, epic: bool, title: str, motivation: str, resolution: list[str], tags: list[str]) -> tuple[str, str]:
    """(model, reason) from the task's own fields."""
    text = " ".join([title, motivation, *resolution, *tags])
    items, size = len(resolution), len(text)
    hard = sorted({m.group(0).lower() for m in _HARD.finditer(text)})
    if epic:
        return OPUS_5_5, "epic"
    if hard:
        return OPUS_5_5, f"hard-keywords={','.join(hard)}"
    if items >= 5 or size > 1500:
        return OPUS_5, f"large items={items} chars={size}"
    if items >= 3 or size > 500:
        return SONNET_5_5, f"medium items={items} chars={size}"
    return SONNET_5, f"small items={items} chars={size}"


def for_prompt(prompt: str | None) -> tuple[str, str]:
    """(model, reason) from a plain tab's prompt."""
    words = len((prompt or "").split())
    hard = sorted({m.group(0).lower() for m in _HARD.finditer(prompt or "")})
    if hard:
        return OPUS_5_5, f"hard-keywords={','.join(hard)}"
    if words > 60:
        return OPUS_5, f"long prompt words={words}"
    if words > 15:
        return SONNET_5_5, f"medium prompt words={words}"
    return SONNET_5, f"short prompt words={words}"


def one_line(text: str | None) -> str:
    """`text` with every run of whitespace (newlines included) collapsed, so a reason stays one log line."""
    return " ".join((text or "").split())


def pick(override: str | None, decide: Callable[[], tuple[str, str]],
         reason: str | None = None) -> tuple[str | None, str]:
    """(model | None, source). A caller's override wins and `decide` is never called; its source is
    `caller: <reason, one line>`, or `caller (no reason given)`. A failing or unknown-id decision is
    (None, `fallback:<why>`), which launches on claude's default."""
    if override and override.strip():
        why = one_line(reason)
        return override.strip(), f"caller: {why}" if why else "caller (no reason given)"
    try:
        model, reason = decide()
    except Exception as e:  # a convenience: whatever went wrong, the tab still opens
        return None, f"fallback:{type(e).__name__}"
    if model not in TIERS:
        return None, f"fallback:unknown-id={model}"
    return model, reason
