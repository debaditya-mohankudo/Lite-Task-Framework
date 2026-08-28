# 1. Framework

The entry point for any work that will take more than one step.

## Lifecycle

```
create ──▶ groom ──▶ implement ──▶ finish ──▶ introspect
```

A task carries one `epic` boolean:

- **epic (`epic=True`)** — groups related work. Has no parent.
- **task (`epic=False`, the default)** — does the work. May have an epic
  parent, or none.

That is the entire hierarchy. There is no story, bug, or subtask, no `type`
enum to get wrong, and no matrix governing what may parent what. If you want a
bug distinguished from a feature, use a tag.

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
tasks__create(title=..., parent=<epic>, motivation=..., resolution=[...])
tasks__set_active(task_id)
tasks__context(task_id)      # ← you must call this; nothing is injected
```

`tasks__set_active` persists per workspace and survives restarts. It is a
convenience so `tasks__context()` can be called with no argument — it is not a
context mechanism in its own right.

## Resuming work

```
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

Then run [introspection](05-introspection.md). It is the step that makes the
next task cheaper, and the easiest one to skip.

## When not to use this

Ephemeral within-session steps do not need tasks. A task is worth creating when
it will outlive the conversation, when someone will want to know why the code
looks like this, or when work will be handed off. Tracking three trivial steps
produces noise that makes the real tasks harder to find.
