"""mcp_tools/tool-graph.json — the derived parts must match the code.

The file's tool set, hooks and used_by relations come from
mcp_tools/gen_tool_graph.py, which reads taskfw/mcp_server.py and the skill
files. A stale file fails here; the fix is to run the script, not to edit the
file. `use` prose is only checked for being present, never for accuracy.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from taskfw import mcp_server as m

ROOT = Path(__file__).parent.parent
spec = importlib.util.spec_from_file_location("gen_tool_graph", ROOT / "mcp_tools" / "gen_tool_graph.py")
gen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gen)

GRAPH = json.loads(gen.GRAPH.read_text())


def test_file_is_what_the_generator_writes():
    """Run `.venv/bin/python mcp_tools/gen_tool_graph.py` when this fails."""
    assert gen.GRAPH.read_text() == gen.render(gen.build(GRAPH))


def test_tool_set_is_the_registered_surface():
    """The AST derivation agrees with what the server actually registers."""
    live = {n for n in dir(m) if n.startswith(("tasks__", "concept__", "task_memory__"))}
    assert set(GRAPH["tools"]) == live


def test_every_tool_has_use_text():
    assert [n for n, t in GRAPH["tools"].items() if not t["use"].strip()] == []


def test_every_relation_joins_a_known_tool_and_skill():
    for r in GRAPH["relations"]:
        assert r["subject"] in GRAPH["tools"] and r["object"] in GRAPH["skills"], r


def test_grooming_edges_for_loop_memory_exist():
    """The drift that motivated the generator."""
    edges = {(r["subject"], r["object"]) for r in GRAPH["relations"]}
    assert ("task_memory__recall", "task-grooming") in edges
    assert ("task_memory__link", "task-grooming") in edges
