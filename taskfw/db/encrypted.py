"""Open an encrypted tasks.db without a second copy of the key or the opener.

The opener, the key and the sqlite3-shaped adapter live in cli-graph's
`mac/encdb.py` (task:8b0561d1). This module loads that one file by path, so
there is a single implementation and a single key file (`secrets/memory.key`
in that repo). Nothing is imported for a plain file.

Location: TASKFW_ENCDB_PATH, else `../cli-graph/mac/encdb.py` next to this repo.
Every failure raises EncryptedDBError saying what to fix; there is no fallback
to a plain open, which would only report "file is not a database".
"""
from __future__ import annotations

import importlib.util
import os
import sqlite3
from pathlib import Path
from types import ModuleType

PLAIN_HEADER = b"SQLite format 3\x00"
_REPO = Path(__file__).resolve().parents[2]
_module: ModuleType | None = None


class EncryptedDBError(RuntimeError):
    """The encrypted DB cannot be opened; the message says why."""


def is_encrypted(path: Path | str) -> bool:
    """True when the file exists, is non-empty and is not plain SQLite."""
    try:
        with open(path, "rb") as f:
            head = f.read(len(PLAIN_HEADER))
    except FileNotFoundError:
        return False
    return head not in (b"", PLAIN_HEADER)


def _encdb() -> ModuleType:
    global _module
    if _module is None:
        src = Path(os.environ.get("TASKFW_ENCDB_PATH") or _REPO.parent / "cli-graph" / "mac" / "encdb.py")
        if not src.is_file():
            raise EncryptedDBError(
                f"{src} not found; set TASKFW_ENCDB_PATH to cli-graph's mac/encdb.py"
            )
        spec = importlib.util.spec_from_file_location("_taskfw_encdb", src)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _module = mod
    return _module


def open_encrypted(path: Path | str, *, readonly: bool = False, timeout: float = 5.0):
    """A sqlite3-shaped connection to an encrypted DB (errors arrive as sqlite3's)."""
    mod = _encdb()
    try:
        return mod.open_db(path, readonly=readonly)
    except mod.EncDBError as e:
        raise EncryptedDBError(str(e)) from e


def open_any(path: Path | str, *, readonly: bool = False, timeout: float = 5.0):
    """Plain files open with stdlib sqlite3 exactly as before; ciphertext through the shared opener."""
    if is_encrypted(path):
        return open_encrypted(path, readonly=readonly, timeout=timeout)
    if readonly:
        return sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro", uri=True, timeout=timeout)
    return sqlite3.connect(str(path), timeout=timeout)
