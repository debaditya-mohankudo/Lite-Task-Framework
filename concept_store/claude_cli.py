"""One-shot `claude -p` completion helper (task:a91133b8).

Ported from SeniorDevAgent's extraction/llm.py::ClaudeCLILLM — ported, not
imported: separate repo, separate dependency stack, so this is a
deliberate copy of the parts that matter for concept extraction, not a
cross-repo import. Structured JSON output parsing, is_error checking, and
timeout/heartbeat handling that langchain_learning/subagent.py's
BareClaudeAgent doesn't have.

Deliberately NOT ported: --session-id/--resume session reuse.
ClaudeCLILLM needs that because Pass 2/Pass 3 make many calls across one
run and want prompt-cache reuse; concept extraction here is genuinely
one-shot per call (one prompt, one response), so there's no session to
reuse and no cost-growth tradeoff to manage.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import threading
import time

log = logging.getLogger(__name__)

_HEARTBEAT_INTERVAL_S = 20


class ClaudeCLI:
    """complete(system, user) -> str via `claude -p --output-format json`.

    Auth via the existing Claude Code login (OAuth/keychain) — no
    ANTHROPIC_API_KEY needed, same as BareClaudeAgent, just with more
    robust call handling.
    """

    def __init__(self, model: str = "sonnet", timeout: int = 600):
        self._model = model
        self._claude_path = os.environ.get("CLAUDE_CLI_PATH") or shutil.which("claude")
        if not self._claude_path:
            raise RuntimeError("claude CLI not found on PATH (set CLAUDE_CLI_PATH)")
        self._timeout = timeout

    def complete(self, system: str, user: str) -> str:
        cmd = [
            self._claude_path, "-p",
            "--safe-mode",
            "--output-format", "json",
            "--tools", "none",
            "--model", self._model,
        ]
        if system:
            cmd += ["--append-system-prompt", system]

        log.debug("ClaudeCLI.complete: model=%s", self._model)

        proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        done = threading.Event()
        start = time.monotonic()

        def _heartbeat():
            while not done.wait(_HEARTBEAT_INTERVAL_S):
                log.info(
                    "ClaudeCLI.complete: still running (%ds elapsed, model=%s)",
                    int(time.monotonic() - start), self._model,
                )

        hb_thread = threading.Thread(target=_heartbeat, daemon=True)
        hb_thread.start()
        try:
            stdout, stderr = proc.communicate(input=user, timeout=self._timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            log.error("ClaudeCLI.complete: timed out after %ds (model=%s)", self._timeout, self._model, exc_info=True)
            raise
        finally:
            done.set()
            hb_thread.join(timeout=1)

        if proc.returncode != 0:
            log.error(
                "ClaudeCLI.complete: claude CLI exited %d (model=%s): %s",
                proc.returncode, self._model, stderr[:500],
            )
            raise RuntimeError(f"claude CLI exited {proc.returncode}: {stderr[:500]}")

        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            log.error("ClaudeCLI.complete: non-JSON output from claude CLI: %s", stdout[:500], exc_info=True)
            raise

        if data.get("is_error"):
            log.error("ClaudeCLI.complete: claude CLI reported an error: %s", data.get("result"))
            raise RuntimeError(f"claude CLI error: {data.get('result')}")

        content = data.get("result", "")
        log.info(
            "ClaudeCLI.complete: model=%s response_len=%d cost_usd=%s",
            self._model, len(content), data.get("total_cost_usd"),
        )
        return content
