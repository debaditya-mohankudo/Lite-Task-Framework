"""Regenerate the derived parts of mcp_tools/tool-graph.json.

    .venv/bin/python mcp_tools/gen_tool_graph.py          # rewrite the file
    .venv/bin/python mcp_tools/gen_tool_graph.py --check  # exit 1 if it is stale

Derived (never hand-edited, rewritten here):
  - the tool set and its order: every function decorated `@_tool(...)` in
    taskfw/mcp_server.py
  - each tool's `namespace` (its name prefix) and `hooks` (the nudge hooks named
    in its decorator, `combine(...)` unpacked)
  - the `used_by` relations: every tool name a .claude/skills/*/skill.md mentions

Hand-written (preserved across a rewrite): `use`, `namespaces`, `skills`, `meta`.
A new tool arrives with an empty `use`, which tests/test_tool_graph.py refuses,
so the only thing left to a person is the prose.

Reads source text only, so it runs under any python; no server import.
"""
from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
GRAPH = ROOT / "mcp_tools" / "tool-graph.json"
SERVER = ROOT / "taskfw" / "mcp_server.py"
SKILLS = ROOT / ".claude" / "skills"

TOOL_REF = re.compile(r"\b(task_memory__\w+|tasks__\w+|concept__\w+)")


def registered() -> dict[str, list[str]]:
    """{tool name: hook names} in source order, from the @_tool decorators."""
    out: dict[str, list[str]] = {}
    for node in ast.parse(SERVER.read_text()).body:
        if not isinstance(node, ast.FunctionDef):
            continue
        for dec in node.decorator_list:
            call = dec if isinstance(dec, ast.Call) else None
            func = call.func if call else dec
            if not (isinstance(func, ast.Name) and func.id == "_tool"):
                continue
            hooks: list[str] = []
            for kw in (call.keywords if call else []):
                if kw.arg == "hook":
                    # combine(a, b) contributes a and b; the `dispatcher.combine`
                    # callee is an Attribute, so only its arguments are Names here.
                    hooks = [n.id for a in (kw.value.args if isinstance(kw.value, ast.Call) else [kw.value])
                             for n in ast.walk(a) if isinstance(n, ast.Name)]
            out[node.name] = hooks
    return out


def skill_edges(tools: set[str]) -> dict[str, set[str]]:
    """{skill: tools it mentions}, restricted to registered tools."""
    return {
        p.parent.name: set(TOOL_REF.findall(p.read_text())) & tools
        for p in sorted(SKILLS.glob("*/skill.md"))
    }


def build(existing: dict) -> dict:
    reg = registered()
    edges = skill_edges(set(reg))
    old_tools = existing.get("tools", {})
    skills = list(existing.get("skills", {}))
    skills += [s for s in edges if s not in skills]
    tools = {
        name: {
            "namespace": name.split("__")[0],
            "use": old_tools.get(name, {}).get("use", ""),
            "hooks": hooks,
        }
        for name, hooks in reg.items()
    }
    relations = [
        {"subject": t, "predicate": "used_by", "object": s}
        for s in skills
        for t in reg
        if t in edges.get(s, ())
    ]
    return {**existing, "tools": tools, "relations": relations}


def render(graph: dict) -> str:
    """indent=2 at the top, one line per tool / relation so a diff reads as one row."""
    parts = []
    for key, val in graph.items():
        if key == "tools":
            rows = ",\n".join(f"    {json.dumps(k)}: {json.dumps(v)}" for k, v in val.items())
            parts.append(f'  "tools": {{\n{rows}\n  }}')
        elif key == "relations":
            rows = ",\n".join(f"    {json.dumps(r)}" for r in val)
            parts.append(f'  "relations": [\n{rows}\n  ]')
        else:
            parts.append(f"  {json.dumps(key)}: " + json.dumps(val, indent=2).replace("\n", "\n  "))
    return "{\n" + ",\n".join(parts) + "\n}\n"


def main(argv: list[str]) -> int:
    existing = json.loads(GRAPH.read_text())
    text = render(build(existing))
    if "--check" in argv:
        return 0 if GRAPH.read_text() == text else 1
    GRAPH.write_text(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
