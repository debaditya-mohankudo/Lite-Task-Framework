"""Change graph — blast radius from real structure, not authored plans.

Grooming's "name the blast radius" step (docs/methodology/03-grooming.md) is
manual grep + read today. This computes the same answer mechanically, on
read, from two things that already exist and cannot disagree with each
other: the AST of the code at HEAD, and real git history.

Deliberately NOT a persisted graph. A hand-authored or cached change graph
would drift from the code the moment either one changes without the other
(CLAUDE.md: "if a value is computed on read, it cannot disagree with its
source"). Every call here re-derives its answer from the working tree and
git log directly.

Two signals, combined:
  - static edges: import/call relationships read straight from the AST —
    what COULD be affected.
  - co-change history: files that have actually changed together in real
    commits touching the target — what HAS mattered in practice.
An edge present in both is "corroborated": a static relationship that real
history backs up, which is stronger evidence than either alone.

Scoped per-target, not repo-wide, which is what keeps a query cheap
regardless of total repo size: `git log -- <path>` is bounded by that file's
own churn, not by the size of the whole history.

Derived from the task:a1324b82 spike. Two things were tried and rejected
there before landing on this shape:
  - a hand-authored plan-graph (grooming typing out intended changes) —
    rejected up front: an authored prediction can drift from what actually
    happens, which is exactly the class of unchecked claim this framework
    avoids everywhere else.
  - symbol-level churn via before/after AST name-diff (did this def get
    added or removed) — built, then rejected on real data: it only catches a
    symbol being added/removed, never one whose body changed with its name
    untouched, so it silently missed the busiest real hotspot in this repo's
    own history (taskfw/mcp_server.py, 8 touches, 0 symbol nodes). Not
    replaced with a body-hash version — relationships (calls/imports), not
    churn detection, are the actual signal grooming needs.
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from taskfw.gitutil import run_git
from taskfw.log import get_logger

log = get_logger(__name__)

#: git log -- <path> is bounded by real churn on that file; this just caps
#: pathological cases (a file with thousands of commits) from making one
#: query scan the entire history.
DEFAULT_COMMIT_LIMIT = 200


def _run_git(repo: str, args: list[str]) -> str:
    """stdout on a clean exit, "" for any process-level failure or a non-zero exit.

    A non-zero exit (unknown rev, no match) is not distinguished from a
    process-level failure here — every call site below already treats "no
    output" as "nothing found", so collapsing them costs nothing.
    """
    out = run_git(args, cwd=repo)
    if out is None or out.returncode != 0:
        return ""
    return out.stdout


def _module_path_for_import(repo: Path, mod: str) -> str | None:
    """'taskfw.mcp_server' -> 'taskfw/mcp_server.py' if that file exists in-repo."""
    if not mod:
        return None
    candidate = repo / (mod.replace(".", "/") + ".py")
    if candidate.exists():
        return str(candidate.relative_to(repo))
    return None


def _parse(repo: Path, path: str) -> tuple[set[str], set[str], list[tuple[str, str]]]:
    """Top-level defs, resolved import targets, and (caller, callee_name) calls for one file at HEAD."""
    full = repo / path
    if not full.exists():
        return set(), set(), []
    try:
        tree = ast.parse(full.read_text(errors="ignore"))
    except SyntaxError:
        return set(), set(), []

    top_defs = {n.name for n in tree.body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}

    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            resolved = _module_path_for_import(repo, node.module)
            if resolved and resolved != path:
                imports.add(resolved)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                resolved = _module_path_for_import(repo, alias.name)
                if resolved and resolved != path:
                    imports.add(resolved)

    calls: list[tuple[str, str]] = []
    for fn in [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        for node in ast.walk(fn):
            if isinstance(node, ast.Call):
                target = None
                if isinstance(node.func, ast.Name):
                    target = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    target = node.func.attr
                if target:
                    calls.append((fn.name, target))
    return top_defs, imports, calls


def _co_change(repo: str, path: str, commit_limit: int) -> tuple[dict[str, dict[str, Any]], int]:
    """File-level co-change counts for `path`, from commits that actually touched it."""
    shas = [s for s in _run_git(
        repo, ["log", f"-{commit_limit}", "--format=%H", "--", path],
    ).split("\n") if s]

    counts: dict[str, dict[str, Any]] = {}
    for sha in shas:
        files = [f for f in _run_git(repo, ["show", "--name-only", "--format=", sha]).splitlines() if f.strip()]
        for f in files:
            if f == path:
                continue
            entry = counts.setdefault(f, {"count": 0, "shas": []})
            entry["count"] += 1
            entry["shas"].append(sha[:10])
    return counts, len(shas)


def blast_radius(repo: str, target: str, commit_limit: int = DEFAULT_COMMIT_LIMIT) -> dict[str, Any]:
    """What a file (or `file.py::symbol`) touches, is touched by, and has actually co-changed with.

    `target` is a repo-relative path, optionally suffixed with `::symbol` to
    narrow calls_out/called_by to one top-level function or class. Everything
    returned is computed fresh from the working tree and git log — nothing
    is cached, so nothing here can be stale relative to its own source.
    """
    repo_path = Path(repo)
    if "::" in target:
        file_path, symbol = target.split("::", 1)
    else:
        file_path, symbol = target, None

    if not (repo_path / file_path).exists():
        return {"error": f"{file_path!r} does not exist in {repo!r}"}

    defs, imports, calls = _parse(repo_path, file_path)
    if symbol and symbol not in defs:
        return {"error": f"{symbol!r} is not a top-level def/class in {file_path!r}"}

    # Files that import this one — found by grep (cheap, repo-wide), confirmed
    # by parsing each candidate rather than trusting the text match alone.
    module_dotted = file_path[:-3].replace("/", ".") if file_path.endswith(".py") else ""
    imported_by: list[str] = []
    called_by: list[str] = []
    if module_dotted:
        grep_out = _run_git(
            repo, ["grep", "-l", "-E", rf"(^import {module_dotted}\b|^from {module_dotted} import)"],
        )
        for cand in sorted(f for f in grep_out.splitlines() if f.strip() and f != file_path):
            _, c_imports, c_calls = _parse(repo_path, cand)
            if file_path not in c_imports:
                continue
            imported_by.append(cand)
            for caller, name in c_calls:
                if symbol and name == symbol:
                    called_by.append(f"{cand}::{caller}")
                elif not symbol and name in defs:
                    called_by.append(f"{cand}::{caller} -> {name}")

    calls_out: list[str] = []
    call_pairs = [c for c in calls if not symbol or c[0] == symbol]
    for caller, name in call_pairs:
        if name in defs and name != caller:
            calls_out.append(f"{file_path}::{name}")
            continue
        for imp in sorted(imports):
            imp_defs, _, _ = _parse(repo_path, imp)
            if name in imp_defs:
                calls_out.append(f"{imp}::{name}")
                break

    co_change, commits_touching_target = _co_change(repo, file_path, commit_limit)
    ranked_co_change = sorted(
        ({"file": f, **v} for f, v in co_change.items()),
        key=lambda e: -e["count"],
    )

    co_changed_set = set(co_change.keys())
    static_related = set(imports) | set(imported_by)
    corroborated = sorted(static_related & co_changed_set)

    return {
        "target": target,
        "commits_touching_target": commits_touching_target,
        "imports": sorted(imports),
        "imported_by": imported_by,
        "calls_out": sorted(set(calls_out)),
        "called_by": sorted(set(called_by)),
        "co_changed_with": ranked_co_change,
        "corroborated": corroborated,
    }
