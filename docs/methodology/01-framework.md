# 1. Framework

The entry point for any work that will take more than one step.

## Lifecycle

```
create ──▶ groom ──▶ implement ──▶ finish ──▶ introspect
```

A task carries one `epic` boolean:

- **epic (`epic=True`)** — groups related work. Has no parent.
- **task (`epic=False`, the default)** — does the work. Its parent is
  usually an epic, but may be any task it is a breakdown of — or none.

That is the entire hierarchy. There is no story, bug, or subtask, no `type`
enum to get wrong, and no matrix governing what may parent what — only an epic
is barred from having a parent. If you want a bug distinguished from a
feature, use a tag.

## Statuses

```
open ──▶ done
  │  ╲
  │   ╲──▶ blocked ──▶ open
  ▼        │
abandoned ◀┘
```

`done` and `abandoned` are terminal. Anything non-terminal may be abandoned.
Re-saving a task at its current status is always allowed, so `tasks__finish` on
an already-finished task succeeds rather than erroring.

## Starting work

```
tasks__create(title=..., epic=True, motivation=...)        # if grouping
tasks__create(title=..., parent=<parent>, motivation=..., resolution=[...])
tasks__context(task_id)      # ← you must call this; nothing is injected
```

`tasks__create` sets the new task active for the workspace — creation is the
start of a loop pass, so you do not call `tasks__set_active` after it (epics
activate the same way). The active pointer is a single in-memory value per
workspace: not persisted, gone on restart. It is a convenience so
`tasks__context()` can be called with no argument — not a context mechanism in
its own right. Nothing clears it automatically; it names its task until
`tasks__add_introspection` files a report against it, which the introspection
pass does at its final step. Recording the introspection IS the deactivation —
there is no standalone clear tool, symmetric with `tasks__create` being the
activation.

## Resuming work

```
tasks__set_active(task_id)   # resuming an older task — create didn't run this session
tasks__active()              # what was I on?
tasks__context()             # the whole bundle for it
```

`tasks__context` returns, in this order: the task, decisions, grooming
findings, the graph (parent/children/edges), commits, and full-text
neighbours. If the bundle exceeded its budget, a `truncated` list names the
sections that were dropped — an omitted section is never silently
indistinguishable from an empty one.

Use `verbosity="summary"` for identity, status, and open checklist items only.

## Ending work

```
tasks__finish(task_id, reason="what actually shipped")
```

`tasks__finish` does not deactivate the task — it stays active so introspection
can pick it up without a lookup. Then run [introspection](05-introspection.md),
which clears the pointer at its final step. It is the step that makes the next
task cheaper, and the easiest one to skip.

## When not to use this

Ephemeral within-session steps do not need tasks. A task is worth creating when
it will outlive the conversation, when someone will want to know why the code
looks like this, or when work will be handed off. Tracking three trivial steps
produces noise that makes the real tasks harder to find.
