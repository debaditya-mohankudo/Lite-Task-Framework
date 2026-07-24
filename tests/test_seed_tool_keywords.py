"""Tests for seed_all_tool_keywords (task:53c9f817 — cold-start keyword seeding)."""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from src.db.schema import MCP_TOOL_HINTS_DDL
from langchain_learning.nodes.log_tool_usage import seed_all_tool_keywords


def _make_hints_db(rows: list[dict] | None = None) -> Path:
    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    conn = sqlite3.connect(tmp.name)
    conn.executescript(MCP_TOOL_HINTS_DDL)
    if rows:
        conn.executemany(
            "INSERT INTO mcp_tool_hints (tool_name, domain, skill, count, keywords) "
            "VALUES (:tool_name,:domain,:skill,:count,:keywords)",
            rows,
        )
    conn.commit()
    conn.close()
    return Path(tmp.name)


@pytest.fixture
def empty_hints_db():
    return _make_hints_db()


def test_seed_all_tool_keywords_inserts_every_registered_tool(empty_hints_db):
    import langchain_learning.nodes.log_tool_usage as lgu
    cfg = type("Cfg", (), {"tool_hints_db": empty_hints_db})()
    with patch.object(lgu, "_cfg", cfg):
        seeded = seed_all_tool_keywords()

    conn = sqlite3.connect(str(empty_hints_db))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT tool_name, count, keywords FROM mcp_tool_hints").fetchall()
    conn.close()

    tool_names = {r["tool_name"] for r in rows}
    assert seeded == len(rows)
    assert "scratch__set" in tool_names
    assert "concept__upsert" in tool_names
    # every seeded row has non-empty keywords and count=0 (never invoked yet)
    for r in rows:
        assert r["count"] == 0
        assert r["keywords"]


def test_seed_all_tool_keywords_does_not_overwrite_existing_rows(empty_hints_db=None):
    hints_db = _make_hints_db([
        {"tool_name": "scratch__set", "domain": "scratch", "skill": "",
         "count": 7, "keywords": "already,seeded,reactively"},
    ])
    import langchain_learning.nodes.log_tool_usage as lgu
    cfg = type("Cfg", (), {"tool_hints_db": hints_db})()
    with patch.object(lgu, "_cfg", cfg):
        seed_all_tool_keywords()

    conn = sqlite3.connect(str(hints_db))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT count, keywords FROM mcp_tool_hints WHERE tool_name = 'scratch__set'"
    ).fetchone()
    conn.close()

    assert row["count"] == 7
    assert row["keywords"] == "already,seeded,reactively"


def test_seed_all_tool_keywords_missing_db_returns_zero():
    import langchain_learning.nodes.log_tool_usage as lgu
    cfg = type("Cfg", (), {"tool_hints_db": Path("/nonexistent/tool_hints.sqlite")})()
    with patch.object(lgu, "_cfg", cfg):
        assert seed_all_tool_keywords() == 0
