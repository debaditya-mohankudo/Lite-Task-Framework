"""Offload clearly-small prompts to a cheap-model side window.

A UserPromptSubmit hook cannot switch the session's model, but it can refuse the prompt and
run it elsewhere. A prompt that `is_small` is blocked in the main session and answered by
`claude -p --model <cheap> --resume <sid> --fork-session` in a kitty split beside it. The fork
carries the conversation, so the cheap model is not answering cold; the main session never
sees the turn (its reason line says where it went).

Wrongly offloading costs the user a turn, so everything here leans conservative and fails open:
the prompt is blocked only after kitty has confirmed the side window exists.

  * `is_small` is a whitelist of lookup shapes, not a blacklist of hard ones.
  * Never offloads replies to the main session's pending question (yes/ok/continue), slash
    commands, edits, or anything naming a file, URL or code.
  * The forked child gets OFFLOAD_CHILD_ENV so its own hooks cannot offload again.
  * `touch` the file at DISABLE_FILE to turn it off at runtime; remove it to turn it back on.
"""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Mapping

from .adapter import KittyAdapter, KittyError
from .models import CHEAP_MODEL

OFFLOAD_CHILD_ENV = "KITTY_TASKFW_OFFLOAD_CHILD"
DISABLE_FILE = Path(os.environ.get("KITTY_TASKFW_OFFLOAD_OFF", Path.home() / ".kitty-taskfw" / "offload_off"))
# --resume re-sends the whole transcript to the cheap model; past ~500 KB it will not fit.
MAX_TRANSCRIPT_BYTES = 500_000
MAX_WORDS = 20

_LEAD = re.compile(
    r"^(what('s| is| are| does| did| was)|where('s| is| are| did)|which|who('s| is| are| wrote)?|"
    r"when('s| is| did| was)|how (many|much|long|old|big)|define|what's the difference)\b", re.I)
_ACTION = re.compile(
    r"\b(fix|add|change|update|implement|write|create|build|run|delete|remove|commit|push|deploy|"
    r"install|edit|refactor|rename|restart|revert|make|set|move|merge|test|debug|investigate|check|"
    r"explain|why|plan|review|should)\b", re.I)
_TOUCHES_CODE = re.compile(r"```|https?://|(?:[\w.-]+/)+[\w.-]+|\b\w+\.(?:py|ts|js|json|md|sql|sh|toml|yaml|yml)\b")


def is_small(prompt: str) -> bool:
    text = (prompt or "").strip()
    if not text or text[0] in "/!#@" or "\n" in text:
        return False
    if len(text.split()) > MAX_WORDS:
        return False
    if not _LEAD.match(text):
        return False
    return not (_ACTION.search(text) or _TOUCHES_CODE.search(text))


def disabled() -> bool:
    return DISABLE_FILE.exists()


def child_cmd(claude: str, session_id: str, prompt: str, model: str = CHEAP_MODEL) -> list[str]:
    return [claude, "-p", "--model", model, "--resume", session_id, "--fork-session",
            "--output-format", "text", prompt]


def maybe_offload(payload: dict, env: Mapping[str, str], *, adapter: KittyAdapter | None = None) -> dict | None:
    """Hook output that blocks the prompt, or None to let the main session handle it."""
    try:
        if payload.get("hook_event_name") != "UserPromptSubmit" or env.get(OFFLOAD_CHILD_ENV) or disabled():
            return None
        prompt, sid = payload.get("prompt") or "", payload.get("session_id") or ""
        socket, window = env.get("KITTY_LISTEN_ON"), env.get("KITTY_WINDOW_ID")
        if not (sid and socket and window and window.isdigit() and is_small(prompt)):
            return None
        transcript = Path(payload.get("transcript_path") or "")
        if not transcript.is_file() or transcript.stat().st_size > MAX_TRANSCRIPT_BYTES:
            return None
        claude = shutil.which("claude", path=env.get("PATH"))
        if not claude:
            return None
        ad = adapter or KittyAdapter(socket, check_version=False)
        wid = ad.launch(
            cwd=payload.get("cwd") or os.getcwd(), cmd=child_cmd(claude, sid, prompt),
            env={OFFLOAD_CHILD_ENV: "1", "PATH": env.get("PATH", "")}, tab=False, location="hsplit",
            next_to=f"id:{window}", keep_focus=True, hold=True, title="haiku side")
    except (KittyError, OSError):
        return None
    return {"decision": "block",
            "reason": f"Offloaded to {CHEAP_MODEL} in kitty window {wid}. "
                      f"Touch {DISABLE_FILE} to stop offloading."}
