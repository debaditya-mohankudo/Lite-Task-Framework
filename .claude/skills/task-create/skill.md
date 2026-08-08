---
name: task-create
description: Reference for creating tasks and epics in taskfw — the two types, what each field is for, and how linking works. Use before calling tasks__create or when the user says /task-create.
user-invocable: true
updated: 2026-08-08
doc: docs/methodology/02-create.md
repo: ~/workspace/task-framework/.claude/skills/task-create/skill.md
deployed: ~/.claude/skills/task-create/skill.md
---

Reference for `tasks__create`. The reasoning lives in [02-create.md](../../../docs/methodology/02-create.md).

## There is no body template

A task is a typed object. There are no required sections, no `Type:` line, and no validator that rejects a malformed body — a task that saves is well-formed by construction. Nothing here needs a scaffolding tool to work around a format gate, because there is no format gate.

## Hierarchy

```
epic
└── task
```

- **epic** — groups related work. Cannot have a parent.
- **task** — does the work. May have an epic parent, or none.

That is the whole hierarchy. There is no story, bug, or subtask, and no matrix governing what may parent what. A task under a task is allowed; forbidding it would buy nothing an epic cannot express.

Kind of work — bug, refactor, urgent, research — goes in `tags`, which is exactly why the type system does not carry it.

## Signature

```python
tasks__create(
    title="Short, specific, and readable in a list",
    type="task",                      # or "epic"
    parent="<epic id>",               # optional; never for an epic
    motivation="Why this is worth doing, and what breaks without it.",
    resolution=["first step", "second step"],
    files=["path/one.py", "path/two.py"],
    tags=["area", "kind"],
    notes="Anything the schema did not anticipate.",
)
```

Every argument except `title` is optional. Nothing is auto-filled with `(pending)` or `TBD`, because nothing is required.

Log the invocation once the id comes back:

```python
tasks__log_skill_invocation(skill="task-create", task_id=result["id"])
```

## Frame the task around behaviour, not implementation detail

Describe the observable change: who or what experiences it, the outcome that
must hold, relevant boundaries, failure behaviour, and invariants. A task
should preserve room to choose or improve the implementation during grooming
and execution; do not make a preferred internal design its definition of done
unless that design is itself a required constraint.

Use `motivation` for the user or system consequence, and write `resolution`
items as verifiable behavioural outcomes where possible. Internal steps belong
there only when they are necessary for safety, clarity, or an established
convention. The completion test is whether the promised behaviour holds, not
whether the original implementation sketch was followed exactly.

## What each field is for

**title** — how the task appears in every list, and what full-text search scores against. Specific beats short: "fix logging" tells a future reader nothing; "logging never named the task id" tells them the whole story. Name the file or module, the specific thing changing, and the reason.

- ✓ `"Add grooming accuracy across tasks — grades were write-only in aggregate"`
- ✓ `"Fix commit-capture regex to handle git -C <path> commit"`
- ✗ `"fix gate"` — will not surface as a neighbour for related work
- ✗ `"run tests"` — an activity, not a task

**motivation** — why, not what. The most valuable field six months later and the one most often left thin. Say what breaks if this is not done. If the task exists because something surprised you, record the surprise.

**resolution** — a checklist of concrete steps, each naming a file or module where possible. Progress is derived from it, so it doubles as the completion signal. Vague items produce a task that is never quite done.

**files** — the files this task is expected to touch. Read directly by `tasks__context` and by grooming, so it is not documentation; it is how the system finds relevant work. Wrong here is cheap to fix and expensive to leave.

**tags** — free-form. Where `bug`, `refactor`, and `urgent` live.

**notes** — the escape hatch, and deliberate. If a thought does not fit a field it goes here rather than being lost.

## Checklist items

```python
tasks__check_item(task_id, index=0, done=True)
```

Zero-based. Progress is computed from the list on every read, so it can never disagree with the checklist it counts. There is no separate progress field to update.

## Updating

```python
tasks__update(task_id, motivation="...", resolution=["..."], tags=["..."])
```

**Every field is replace, not append**, and that is explicit per field. Pass only what you are changing. To add one checklist item, pass the whole list including the existing items.

## Linking

```python
tasks__link(from_id, to_id, rel="depends_on")     # idempotent
tasks__unlink(from_id, to_id, rel="depends_on")   # omit rel to remove all
tasks__edges(task_id)                             # both directions
```

Enforced vocabulary (`TASK_EDGE_RELATIONS`, checked on `tasks__link` — not `tasks__unlink`, which must always remove any existing edge): `depends_on`, `blocks`, `relates_to`, `duplicates`, `removes`, `implements`, `supersedes`, `reverts`, `writes_to`, `extends`. An unrecognised relation is rejected.

Record sequencing as edges rather than only in prose. Prose is for a human reading one task; edges are what lets anything else reason about order.

## Concept lookup

Where the repo has a concept store, check whether the files this task touches already have concepts:

```python
concept__list(repo="<abs repo path>")
concept__search(repo="<abs repo path>", query="<module or idea>")
```

Match against each concept's `module` field and note the slugs in `notes` or `tags`, so grooming and introspection can look them up without re-scanning. `repo` is always required — a store is never tied to whichever server is running.

Skip silently if the repo has no store.

## Rules

- **An epic cannot have a parent.** A task cannot be its own parent. Those are the only two structural rules; both return `{"error": ..., "rule": ...}`, and `rule` names which one fired so you can react without matching on message text.
- **Use `tags`, not new types**, for anything the two types do not express.
- **Write `resolution` as concrete steps naming files** for anything with three or more discrete targets.
- **Specify observable behaviour before internal design.** Keep implementation
  choices flexible unless they are a real constraint.
- Activate after creating: `tasks__set_active(task_id)` — then call `tasks__context`.
