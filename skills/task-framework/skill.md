---
name: task-framework
description: Start or resume a task using the task graph framework. Creates a task, activates it for the session, and explains the lifecycle. Use when the user runs /task-framework or asks to work on a task with tracking.
user-invocable: true
updated: 2026-06-11
wiki: "[[Documentation/Tools/claude-hooks/skills.md]]"
---

<!-- source of truth: ~/workspace/claude-hooks/docs/skills.md -->

You are now operating in task-framework mode. Read these instructions carefully — they define how to use the task graph throughout this session.

## What the task framework does

Every task you work on has a lifecycle tracked in `proj_tasks.db`:

```
tasks__create      →  task is open
tasks__set_active  →  task becomes wip; session_id bound in checkpoint
  (each UPS turn)  →  task history injected into your system prompt automatically
/gc                →  commit while task is active (appends task:<id> to commit body)
task:<id> done     →  auto-closes task at session stop (keyword detection)
tasks__finish      →  explicit close with reason
```

The active task's full turn history (summary + tools used, current session only, oldest-first) is injected into your `## Task history (this session)` system prompt block automatically — you don't need to ask for it.

## Getting the session_id

The session_id is always in your `## Turn state` system prompt block:

```
## Turn state
- session_id: <uuid>
- prompt_id: <uuid>
```

There is no MCP tool for this — always read from `## Turn state`.

## Steps when invoked with a task description

### 0. Assess decomposition

Before creating, assess if the task has 2–3 clearly distinct phases that can be worked sequentially. If yes:
- **Check the concept store first** (if the repo has one — `concept_store/concepts.json` or `concepts.db`, see `/task-grooming` Step 2 for format detection). Look up which concepts match the files/modules this task touches (`concept__list`/`concept__get`, or read the JSON directly). Documented concepts encode existing architectural/module boundaries — let the split follow them: don't fragment a change that lives entirely inside one concept's evidence into multiple subtasks; do split along concept boundaries when the task genuinely spans several distinct ones. This is a proposal input, not a hard rule — phase/sequence still matters too, but a split that cuts against a documented boundary is a signal to reconsider, not just proceed.
- Propose the subtask list to the user (one line each) with the intended sequence
- Get confirmation before creating
- **Create a parent task first** (e.g. `"Portfolio DB — implement JSON storage"`)
- Create each subtask passing `parent_id=<parent_task_id>` — this tags them `parent:<id>` automatically, enabling hierarchy display and auto-close of parent when all subtasks are done
- Activate the first subtask; work sequentially

If the task is a single coherent piece of work, skip this step and create one task directly. Don't force a split.

If no concept store exists for the repo, skip the concept-store check silently and decompose on phase/sequence alone.

### 0b. Pre-implementation grooming

After all subtasks are created — before writing any code — run `/task-grooming epic:<parent_id>` (or `/task-grooming task:<id>` for a single task).

The grooming skill activates each task, reads injected related-task and diff-RAG context, audits the body for gaps (missing file paths, deferred decisions, conflicts with prior work), updates each body with findings, then resets status back to `open`.

**Why:** injected related tasks surface prior art and peer work automatically. Gaps caught here cost nothing; gaps caught mid-implementation cost a revert and a replan.

Skip this step only for single-task work with no subtasks.

### 1. Create the task

Use `/task-create` — it documents the full API surface (issue hierarchy, cwd vs domain, body format, subtask signatures). Quick reference:

```python
# Dev task
mcp__claude-hooks__tasks__create(title="...", body="...", cwd="<repo path>")

# Research / non-dev
mcp__claude-hooks__tasks__create(title="...", body="...", domain="<domain>")
```

**Title quality (dev tasks):** The title is embedded for semantic neighbor search — it must encode *what + where + why* with concrete keywords. A good title is self-contained and scoped:
- ✓ `"Add memories column to task_events for per-turn injection logging"`
- ✓ `"Fix GitCommitGate regex to handle git -C <path> commit"`
- ✗ `"fix gate"` — too vague, won't surface as a neighbor for related work
- ✗ `"run tests"` — activity, not a task

Use the file/module name, the specific thing being changed, and the reason. These keywords are what `load_related_tasks` scores against when injecting past context.

**Checklist format:** For removal, refactor, or any task with 3+ discrete file/step targets, write `Resolution:` (or `Notes:`) as a markdown checklist — not prose. Tick items with `- [x]` via `tasks__update(body=...)` as each step completes.

```text
Resolution:
- [ ] src/tools/tasks.py — remove X
- [ ] hooks/gates.py — remove Y
- [ ] delete load_active_review.py
```

### 2. Activate it for this session

**This is not optional bookkeeping.** Activation triggers context injection: related past tasks, related commits (diff RAG), and scored memories are all injected into your system prompt automatically from this point on. Without activation you work blind; with it, every subsequent turn has full context.

```python
mcp__claude-hooks__tasks__set_active(task_id="<task_id>", session_id="<session_id from Turn state>")
```

If you get `No module named langgraph` (MCP env issue), fall back to the script:

```bash
cd ~/workspace/claude-hooks && uv run python scripts/task_activate.py activate <task_id> <session_id>
```

### 3. Confirm to the user

```
Task task:<id> active — <title>
Tracking turns and tools for this session. Say "task:<id> done" when finished.
```

### 4. Work on the task normally

Follow `/task-implementation`'s execution loop (understand → think → implement → validate → reflect) while working — it's the behavioral guide for this step: stay in scope, validate assumptions early, finish decisively rather than polishing indefinitely.

- Use `tasks__list()` to see open/wip tasks (add `limit=N` if you need more than the default 50)
- Use `tasks__history(id)` to inspect logged turn events
- Use `tasks__update(id, body=...)` to append notes mid-task

**Finding code while working:**
```python
mcp__claude-hooks__code_rag__smart_search(query="<symbol or concept>", repo="<abs path>")
mcp__claude-hooks__code_rag__index_files(files=["relative/path/to/file.py"], repo="<abs path>")
```

### 5. Commit before closing

**Use `/gc` for each subtask commit** — commits without pushing, appends `task:<id>` to commit body while task is active.

**Push manually after the parent task is closed.**

Order: **implement → `/gc` (per subtask) → close parent task → `git push`**

### 6. Closing the task

**Preferred — say it in your message to the user:**
```
task:<id> done
```

**Explicit:**
```python
mcp__claude-hooks__tasks__finish(task_id="<id>", session_id="<session_id>", reason="<what was accomplished>")
```

## Steps when invoked without a task description

List open tasks: `mcp__claude-hooks__tasks__list()` — display and ask which to activate or whether to create a new one.

## Rules

- **Create and activate a task before any code change.** No exceptions — even for one-liners. Activation is what unlocks related-task, related-commit, and memory injection. Skip it and you work without context.
- **Use checklist format in `Resolution:` for tasks with 3+ discrete steps.** Update with `- [x]` as each step completes.
- **One active task per session.** Call `tasks__clear_active` first if one exists.
- **Never guess the session_id.** Always read from `## Turn state`.
- Mark tasks `done` promptly. Stale `wip` tasks accumulate stale memories.
- **Commit before closing.** `/gc` while task is active so commit gets `task:<id>`.
- **Push after parent task closes**, not before.
