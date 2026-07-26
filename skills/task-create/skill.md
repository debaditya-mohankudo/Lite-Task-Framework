---
name: task-create
description: Quick reference for creating Jira-style issues — epic, story, task, bug, subtask. Which args to pass, hierarchy rules, when to use cwd vs domain. Use when about to call tasks__create or when the user says /task-create.
user-invocable: true
updated: 2026-07-26
wiki: "[[Documentation/Tools/claude-hooks/skills.md]]"
repo: ~/workspace/claude-hooks/skills/task-create/skill.md
deployed: ~/.claude/skills/task-create/skill.md
---

Reference for `mcp__claude-hooks__tasks__create`. Read this before calling it.

## Jira hierarchy

```
epic
└── story / task / bug
    └── subtask
```

- **epic** — large initiative spanning multiple sprints; never a child of another issue
- **story** — user-facing feature; child of an epic
- **task** — technical work item; child of an epic or standalone
- **bug** — something broken; child of an epic or standalone
- **subtask** — smallest unit; must have a parent (story, task, or bug)

Pass `issue_type=` to set the level. Default is `task`.

## Signatures

```python
# Epic — top-level initiative, no parent
# Use tasks__create_epic — builds the required body internally, no body template needed.
mcp__claude-hooks__tasks__create_epic(
    title="<initiative title>",
    motivation="<why this epic exists>",
    cwd="<repo path>",          # or domain=
)

# Story / task / bug — child of an epic
# No epic yet? Use parent_id="96c361de" (Unassigned) — move to a real epic later.
mcp__claude-hooks__tasks__create(
    title="<short title>",
    body="<Type: + template below>",
    cwd="<repo path>",          # or domain=
    parent_id="<epic_task_id>",
    issue_type="story",         # or task | bug
)

# Subtask — must have a parent (story, task, or bug)
mcp__claude-hooks__tasks__create(
    title="<short title>",
    body="<Type: + template below>",
    cwd="<repo path>",          # or domain=
    parent_id="<parent_task_id>",
    issue_type="subtask",
)

# Research / non-dev — explicit domain, no cwd
mcp__claude-hooks__tasks__create(
    title="<short title>",
    body="<Type: + template below>",
    domain="<domain>",
    issue_type="task",          # or story | bug
)
```

## domain values

| domain | When to use |
|--------|-------------|
| `market-intel` | Stock research, portfolio, FII/DII, macro, Nifty/Sensex |
| `vault` | Obsidian notes, docs, writing |
| `astrology` | Jyotish, dasha, chart analysis |
| `claude-hooks` | claude-hooks repo development |
| `macos` | macOS automation, Swift, local tools |
| `global` | Cross-domain or general |

## body format (required)

Always start with `Type:` — pick one: `feature`, `bug`, `research`, `misc`.
This is the **workflow kind** (controls required sections), separate from `issue_type`.

> On-disk copies of these scaffolds live in the claude-hooks repo at `task_templates/`
> (one `.md` per type). The required sections are enforced by `hooks/dispatcher.py`
> → `_TASK_BODY_SECTIONS` — keep all three in sync if you change them.

**feature** — new capability or enhancement
```
Type: feature
Task:
<what is being built>

Resolution:
<what was delivered — fill in after done>

Motivation:
<why this is needed>

Files:
<file1>, <file2>
```

**bug** — something broken that needs fixing
```
Type: bug
Task:
<what is broken and observed behavior>

Resolution:
<what fixed it — fill in after done>

Cause:
<root cause>

Files:
<file1>, <file2>
```

**research** — investigation, analysis, market study
```
Type: research
Task:
<question or hypothesis>

Finding:
<conclusion — fill in after done>

Context:
<what triggered this / background>

Files:
(leave blank)
```

**misc** — refactor, docs, config, cleanup
```
Type: misc
Task:
<what is being done>

Resolution:
<outcome — fill in after done>

Notes:
<any relevant context>

Files:
<file1>, <file2>
```

**epic** — large initiative (normally auto-filled by `tasks__create_epic`, shown here for completeness)
```
Type: epic
Task:
<goal and scope>

Resolution:
[ ] <outcome / stories — or "(pending)" while open>

Notes:
<design decisions, constraints>

Files:
<key files>
```

**feedback** — task-specific learning captured post-completion (created automatically by `tasks__create_feedback` from `/task-introspection` Step 4 — not normally hand-written)
```
Type: feedback
Decision:
<design decision made and why — optional>

Constraint:
<constraint or gotcha discovered — optional>

Pattern:
<pattern that worked or failed — optional>
```
At least one of Decision/Constraint/Pattern is required. Always parented to the finished task it documents.

## Checklist format in Resolution

For removal, refactor, or any task with 3+ discrete file/step targets, write `Resolution:` as a markdown checklist — not prose. The gate only checks the section exists; content is free-form.

```
Resolution:
- [ ] src/tools/tasks.py — remove _ISSUE_TYPES review entry
- [ ] hooks/gates.py — remove _REVIEW_TAG_RE
- [ ] delete langchain_learning/nodes/load_active_review.py
```

Tick items with `- [x]` via `tasks__update(body=...)` as each is done. This makes the task body a live progress tracker rather than a static plan.

## Concept store lookup (claude-hooks tasks only)

When creating a task for the claude-hooks repo, check if any files in the `Files:` section have stored concepts:

```python
import json
from pathlib import Path
concepts = json.loads(Path("/Users/debaditya/workspace/claude-hooks-dev/concept_store/concepts.json").read_text())
touched = [f.strip() for f in files_section.split(",")]
hits = [(name, c) for name, c in concepts.items() if c["module"] in touched]
```

If hits found, add a `Concepts:` line to the task body listing the matching concept slugs:

```
Files:
hooks/gates.py, src/config.py

Concepts:
gates-prereq-chain-enforcement, gates-commit-traceability, config-db-paths-and-domains
```

This makes it explicit which architectural concepts the task touches, so grooming and introspection can look them up without re-scanning the store.

Skip silently if `concepts.json` doesn't exist, the task is not claude-hooks domain, or no files match.

## Rules

- **Never pass both `cwd` and `domain`** — `domain` takes precedence; pick one.
- **cwd for dev, domain for everything else.**
- **Epics use `tasks__create_epic`** — not `tasks__create`. Pass `title` + `motivation` + `cwd`/`domain`. Never pass `parent_id` or `body` for an epic.
- **No epic yet?** Use `parent_id="96c361de"` (Unassigned epic) — don't let missing hierarchy block task creation. Move to a real epic later.
- **Subtasks must have a parent** — always pass `parent_id` for `issue_type="subtask"`.
- For market-intel research, always use `domain="market-intel"` — never pass a k-mirror path as cwd.
- Always activate after creating: `tasks__set_active(task_id, session_id)`.
- Use checklist format in `Resolution:` for any task with 3+ discrete steps or file targets.
