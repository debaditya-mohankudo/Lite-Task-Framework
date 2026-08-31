# Methodology

The store and the rules are plumbing. **This is the actual product.**

These five documents describe a loop for doing engineering work with an AI
agent, in which each pass leaves the system slightly more capable than the
last. They are plain markdown on purpose: the methodology is the portable
asset, and `/slash-command` invocation is one host's delivery mechanism, not
the thing itself. Package them per host however that host expects.

| # | Document | When |
|---|---|---|
| 1 | [Framework](01-framework.md) | Starting or resuming any multi-step work |
| 2 | [Create](02-create.md) | Writing a task worth reading later |
| 3 | [Grooming](03-grooming.md) | Before implementation begins |
| 4 | [Implementation](04-implementation.md) | While building |
| 5 | [Introspection](05-introspection.md) | After a task closes |

## The loop

```
create ──▶ groom ──▶ implement ──▶ introspect ──┐
   ▲                                            │
   └────────────────────────────────────────────┘
        introspection improves the next create
```

Grooming makes falsifiable predictions. Introspection grades them. That single
feedback edge is what stops grooming from being decorative — without it,
predictions are write-only and nobody ever learns whether the pass was worth
running.

The edge closes twice. Per task, introspection grades that task's predictions.
Across tasks, `tasks__grooming_accuracy` aggregates the grades so grooming
itself can be corrected — "repeated `wrong` grades mean the wrong questions" is
a claim about a series, and one task cannot show a series.

## One difference from prompt-injection systems

Context is **pulled, not pushed**. Nothing is injected into the model's context
automatically; there is no per-turn assembled block. An agent that wants a
task's context calls `tasks__context` and gets the whole bundle in one
response.

The practical consequence for every document here: **activating a task does not
make its context appear.** Call `tasks__context` explicitly, every time — there
is no host-side pointer that reminds you, on purpose. A host that wants a
reminder builds it into the skill that does the activating, not into a hook
that runs behind the agent's back.

## Tool surface

All tools are MCP tools, so any MCP host has them. No session id is required
anywhere — the framework has no session concept, and active task is scoped to
the workspace instead.

Reading: `tasks__context` `tasks__get` `tasks__list` `tasks__search` `tasks__edges` `tasks__active`
Across tasks: `tasks__grooming_accuracy`
Loop memory: `task_memory__record` `task_memory__recall` `task_memory__get` `task_memory__link` `task_memory__supersede` `task_memory__forget`
Writing: `tasks__create` `tasks__update` `tasks__check_item` `tasks__finish` `tasks__add_decision` `tasks__add_introspection`
Graph: `tasks__link` `tasks__unlink` `tasks__add_commit`
Active: `tasks__set_active` `tasks__active` (deactivation is folded into `tasks__add_introspection`)
