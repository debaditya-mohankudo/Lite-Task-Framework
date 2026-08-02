---
name: task-framework
description: Start or resume a task using taskfw. Creates a task, activates it for the workspace, and explains the lifecycle. Use when the user runs /task-framework or asks to work on a task with tracking.
user-invocable: true
updated: 2026-08-01
doc: docs/methodology/01-framework.md
repo: ~/workspace/task-framework/.claude/skills/task-framework/skill.md
deployed: ~/.claude/skills/task-framework/skill.md
---

The entry point for work that will take more than one step.

**The reasoning lives in [01-framework.md](../../../docs/methodology/01-framework.md).** This file is the host wrapper: what to call, in what order. Read the doc for why.

## The one thing that differs from most task systems

**Nothing is injected.** Activating a task does not make its context appear. You must call `tasks__context` explicitly — that call *is* the context mechanism, and there is no per-turn block that fills in behind it.

Where hooks are installed, a one-line pointer names the active task. That is a reminder to pull, not the pull.

## Lifecycle

```
create ──▶ groom ──▶ implement ──▶ finish ──▶ introspect
```

Two types: **epic** groups (no parent), **task** does work. That is the whole hierarchy — no story, bug, or subtask. If you want a bug distinguished from a feature, use a tag.

Four statuses: `open`, `blocked`, `done`, `abandoned`. The last two are terminal.

## Starting work

### 1. Decompose, if it genuinely splits

If the work has two or three distinct sequential phases, propose the split to the user and get confirmation before creating anything. Check the concept store first where the repo has one — `concept__list(repo=...)` — and let existing module boundaries guide the split. A split that cuts against a documented boundary is a signal to reconsider.

If it is one coherent piece of work, create one task. Do not force a split.

### 2. Create

```python
tasks__create(title="...", type="epic", motivation="...")          # if grouping
tasks__create(title="...", parent="<epic id>", motivation="...",
              resolution=["step one", "step two"], files=["a.py"], tags=["area"])
```

There is no body template and no required sections — the object's shape is the schema. See [/task-create](../task-create/skill.md) for what each field is for.

### 3. Groom before writing code

Run [/task-grooming](../task-grooming/skill.md) with `task:<id>` or `epic:<id>`. Skip only for trivial single-task work.

Gaps caught in grooming cost nothing. Gaps caught mid-implementation cost a revert and a replan.

### 4. Activate and pull context

```python
tasks__set_active(task_id)
tasks__context(task_id)      # ← you must call this
```

`tasks__set_active` persists per workspace and survives restarts. It is a convenience so `tasks__context()` can be called with no argument — it is not a context mechanism in its own right.

Confirm to the user:

```
Task <id> active — <title>
```

### 5. Work

Follow [/task-implementation](../task-implementation/skill.md). Tick items as they complete:

```python
tasks__check_item(task_id, index=0, done=True)
tasks__add_decision(task_id, "Chose X over Y because Z.")
```

Find code with Read, Grep, and Glob. The framework deliberately ships no code search of its own.

### 6. Commit

Put `task:<id>` in the commit message. Where the PostToolUse hook is installed the link is recorded automatically; where it is not, `python -m taskfw.backfill` recovers it from git history afterwards.

### 7. Finish, then introspect

```python
tasks__finish(task_id, reason="what actually shipped")
```

Then run [/task-introspection](../task-introspection/skill.md). It is the step that makes the next task cheaper, and the easiest one to skip.

## Resuming

```python
tasks__active()      # what was I on?
tasks__context()     # the whole bundle for it
```

## Invoked with no task description

```python
tasks__list()
```

Display open tasks and ask which to activate, or whether to create a new one.

## Rules

- **Call `tasks__context` after activating.** Activation alone tells you nothing.
- **No session id anywhere.** The framework has no session concept; active task is scoped to the workspace.
- Create a task when the work will outlive the conversation, when someone will want to know why the code looks like this, or when it will be handed off. Tracking three trivial steps produces noise that buries the real tasks.
- `tasks__update` replaces each field you pass. It does not append.
- Mark tasks done promptly.
