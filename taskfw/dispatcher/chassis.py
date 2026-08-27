"""The machinery every advisory nudge runs through: per-call tool logging,
hook composition, and the one shared result-mutation helper.

Nothing here knows what a nudge says or when it fires — that's
taskfw.dispatcher.nudges. Nothing here derives task state either — that's
taskfw.dispatcher.phase. This module is pure mechanism: given a hook
(Callable[[dict], None]), run it at the right moment and log the call
regardless.

`tool_called` is the pre/post-hook shape this module's callers run through.
It is the same shape as claude-hooks' own Bash/MCP gates (task:7b25ee0d
weighed this deliberately before building it) — the difference that made it
worth building here is scope, not kind: it is self-contained inside taskfw's
own server, with no dependency on an external hook process, and it only ever
sees taskfw's own tools, never another server's calls. `post` runs once,
after the block exits with no exception and a result the caller marked `ok`
— a tool that returned an `{"error": ...}` refusal is never nudged.
"""
from __future__ import annotations

from typing import Any, Callable

from taskfw.log import get_logger

log = get_logger(__name__)


def apply_nudge(result: dict[str, Any], key: str, nudge: str | None) -> None:
    """Set `result[key] = nudge`, or leave `result` untouched when there's nothing to say.

    One shared wrapper for every nudge function's result — call the nudge
    function yourself first (its own signature is the real variation between
    introspection_nudge/finish_nudge/finish_reminder_nudge; there is nothing
    left to template once the "if truthy, set the key" step is factored out).

    Logs the firing here, not in each nudge function, so every nudge is
    observable in the `logs` table by construction — a nudge type added
    later gets logged for free just by going through this function, instead
    of remembering to add a log line at each call site.
    """
    if nudge:
        result[key] = nudge
        log.info("nudge=%s fired: %s", key, nudge)


class tool_called:
    """Wraps every MCP tool call: unconditional logging, plus an optional nudge hook.

    Usage:

        with dispatcher.tool_called("tasks__x", post=lambda r: ...) as call:
            call.result = {"ok": True, ...}
            return call.result

    In this codebase there is exactly one tool_called per call, opened by
    the `_tool` decorator in mcp_server.py, which is the one route every
    MCP tool goes through — registration, logging, and hook, all in one
    place (task:58782207). Nothing here is specific to that decorator,
    though; tool_called is usable directly wherever a tool needs it.

    `name` is the tool being called — every exit logs exactly one line,
    `tool=<name> OK` / `tool=<name> REFUSE rule=<rule>` / `tool=<name> ERROR
    <type>: <msg>`. This is the only place in the framework that logs tool
    usage itself, rather than the state changes a tool happened to cause —
    those are store.py's and lifecycle.py's job and unaffected by this.

    `pre` runs on entry. `post` (the "hook") runs on a clean, non-refused
    exit — refused means `call.result` is a dict containing an "error" key.
    A non-dict result (e.g. tasks__list's bare list) is never a refusal, so
    it's treated as success for both logging and the hook; `post` is only
    actually invoked when `call.result` is a dict, since apply_nudge (what
    every hook calls) needs somewhere to write the nudge key. Mutating
    `call.result` in `post` is visible in what the function actually
    returns: `return call.result` evaluates the reference before `__exit__`
    runs as part of unwinding the `with` block, and `post` mutates that same
    dict in place.

    Call sites build `post` with a lambda, not `functools.partial`: `apply_nudge`
    takes `result` first, but `partial` appends new positional args after the
    ones it pre-binds, so `partial(apply_nudge, key, nudge)` called as
    `p(result)` would call `apply_nudge(key, nudge, result)` — the wrong
    order. A lambda reads left-to-right in the same order as the function
    signature it calls; `partial` would need every pre-bound argument passed
    by keyword to avoid that, for no benefit at a single call site.
    """

    def __init__(
        self,
        name: str,
        pre: Callable[[], None] | None = None,
        post: Callable[[dict[str, Any]], None] | None = None,
    ):
        self.name = name
        self.pre = pre
        self.post = post
        self.result: Any = {}

    def __enter__(self) -> "tool_called":
        if self.pre:
            self.pre()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is not None:
            log.info("tool=%s ERROR %s: %s", self.name, exc_type.__name__, exc)
            return
        refused = isinstance(self.result, dict) and "error" in self.result
        if refused:
            log.info("tool=%s REFUSE rule=%s", self.name, self.result.get("rule"))
            return
        log.info("tool=%s OK", self.name)
        if self.post and isinstance(self.result, dict):
            self.post(self.result)


def combine(*hooks: Callable[[dict[str, Any]], None]) -> Callable[[dict[str, Any]], None]:
    """A composite hook: runs each of `hooks` in order, in the same
    Callable[[dict], None] shape as any single one.

    Lets a tool that needs several nudges (e.g. tasks__update wants both
    finish_reminder_nudge and ungroomed_progress_nudge) compose them at its
    own call site instead of tool_called or mcp_server.py's `_tool` needing
    to know the difference between one hook and several (task:58782207).
    Each hook mutates the same `result` dict in place via apply_nudge, so
    order only matters if two hooks ever wrote the same key — none do today.
    """
    def composite(result: dict[str, Any]) -> None:
        for hook in hooks:
            hook(result)
    return composite
