"""Logging — stderr only, deliberately.

An MCP server speaks JSON-RPC over stdout, so anything written there corrupts
the protocol. Logging to stderr is a correctness requirement here, not a
stylistic preference.
"""
from __future__ import annotations

import logging
import sys

from taskfw import config

_configured = False


def get_logger(name: str) -> logging.Logger:
    global _configured
    if not _configured:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root = logging.getLogger("taskfw")
        root.addHandler(handler)
        root.setLevel(config.LOG_LEVEL)
        root.propagate = False
        _configured = True
    return logging.getLogger(f"taskfw.{name}" if not name.startswith("taskfw") else name)
