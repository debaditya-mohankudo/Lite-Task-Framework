---
name: task-implementation
description: Engineering execution philosophy for active tasks. Stay in scope, log decisions as they happen, remove failure modes rather than policing them, and finish decisively. Use while working, after /task-grooming and before /task-introspection.
user-invocable: true
updated: 2026-08-08
doc: docs/methodology/04-implementation.md
repo: ~/workspace/task-framework/.claude/skills/task-implementation/skill.md
deployed: ~/.claude/skills/task-implementation/skill.md
---

Grooming removed the uncertainty. This is about not reintroducing it.

The reasoning lives in [04-implementation.md](../../../docs/methodology/04-implementation.md).

```
/task-grooming  →  implement (this skill)  →  /task-introspection
```

In effect for any active task. Also reach for it when the user says "just implement it", or when work is drifting — repeated searches, expanding scope, debugging without a hypothesis.

## Start

Use the supplied `task_id`. If none was given, read the active task with
`tasks__active()` instead of guessing. `tasks__set_active` pushes task_id onto
a LIFO stack rather than overwriting whatever was active — nothing is lost,
so no confirmation is needed. `tasks__finish`/`tasks__clear_active` pop back
to whatever was pushed underneath automatically.

```python
tasks__set_active(task_id)
tasks__log_skill_invocation(skill="task-implementation/start", task_id=task_id)
tasks__context(task_id)     # read the grooming findings before writing code
```

The grooming findings are the point. Reading the title and starting to type discards the pass that was just paid for.

## Specify before implementing, when the output shape is the hard part

If the difficulty is *what a thing returns* rather than how it works, write the contract in prose first — sections, ordering, limits, failure behaviour — then implement against it.

This turns coding into transcription: no design happens mid-implementation, and tests get written against the stated contract rather than against whatever the code turned out to do. It is the single highest-leverage habit in this loop.

## Be behavioural, not detail-oriented

Lead with what a user or caller can observe: the outcome, boundaries, failure behaviour, and invariants that must hold. Implementation details are a means to that end, not the contract. Prefer guidance and tests that leave room for a better internal design while pinning the behaviour that matters.

When deciding whether to add detail, ask whether it changes an observable promise. If it does not, keep it flexible unless it is needed to make the work safe, understandable, or consistent with an established convention.

## Prefer removing a failure mode over policing it

The recurring technique in this codebase, and worth reaching for first:

- A value derived on read cannot drift from its source, so no reconciliation is needed.
- A migration function with no destructive code path cannot perform a destructive migration, so no review vigilance is needed.
- A typed field cannot be a malformed section, so no body validator is needed.

Before writing a check, ask whether the thing being checked needs to be representable at all. Deleting the failure mode is cheaper than detecting it, forever.

## Log decisions as they happen

```python
tasks__log_skill_invocation(skill="task-implementation/decision", task_id=task_id)
tasks__add_decision(task_id, "Chose X over Y because Z.")
```

Not at the end — at the moment. A decision recorded later is reconstructed, and reconstruction quietly rewrites the reasoning into whatever now seems sensible.

Log one when you chose between real alternatives, rejected the obvious approach, were forced by a constraint, or did something that will look wrong to someone who was not there.

## When something surprises you

Stop and write it down before continuing. A surprise mid-task is the cheapest knowledge you will get all day, and it evaporates within the hour. It belongs in a decision, in `notes`, or in the introspection report.

## Check off deliverables as they land

```python
tasks__log_skill_invocation(skill="task-implementation/check-item", task_id=task_id)
tasks__check_item(task_id, index=..., done=True)
```

The moment an item is actually done — not batched right before `tasks__finish`. A task whose real work landed but whose checklist still reads all-unchecked is indistinguishable from the outside from a task with no progress: "done" and "empty" look the same until someone actually reads it.

If an item can't be checked off — a decision is still open, or what it asked for changed mid-task — say so. Leave it unchecked and log why with `tasks__add_decision` or in `notes`. An unresolved item with no explanation reads as forgotten; one with a stated reason reads as a deliberate open question.

## The loop

```python
tasks__log_skill_invocation(skill="task-implementation/the-loop", task_id=task_id)
```

1. **Understand** — objective, subsystem, existing patterns, constraints. If uncertainty is high, search; if low, implement. Stop searching once you know enough.
2. **Think** — smallest next change, expected outcome, how you will validate it.
3. **Implement** — prefer existing abstractions and conventions. Avoid unrelated cleanup and speculative improvement.
4. **Validate** — immediately. Never build on an unverified assumption.
5. **Reflect** — did reality match expectation? Replan if not.

## Tests

Test the contract, not the implementation. A test asserting what the code already does is a change-detector; one asserting the promise is a regression test.

**Test what fails silently** — logging, fail-open paths, idempotence. Anything whose failure produces no error is exactly what needs a test, because nothing else will ever tell you it broke.

Verify against reality where the cost is small. A real temporary git repository catches what a mocked subprocess cannot, and a live end-to-end run catches what assertions do not.

## When a test fails, decide which side is wrong

Sometimes the test encodes an assumption the design deliberately contradicts. Do not reflexively change the implementation to satisfy it. Ask which one expresses the intended behaviour, then fix the other — and if the design was right, add a test pinning the interaction so the next person does not have the same argument.

## Finish

```python
tasks__log_skill_invocation(skill="task-implementation/finish", task_id=task_id)
tasks__finish(task_id, reason="what actually shipped")
```

The checklist should already be checked off by now (see above) — this call closes the task, it does not reconcile it.

Commit with `task:<id>` in the message, then call `tasks__add_commit(task_id, sha, repo)` yourself — there is no hook that does this automatically, so the link only exists if you make the call. `python -m taskfw.backfill` re-derives it from git history if the call is missed; that is the recovery path, not a substitute for making it.

State outcomes plainly. If tests fail, say so. If a step was skipped, say that. A `reason` that overstates what landed is worse than none, because it will be believed.

## Warning signs

Repeated searches without implementation; repeated edits to the same code; expanding scope; unrelated refactoring; changes without validation; debugging without a hypothesis; polishing after the objective is met.

## Principles

**Reduce uncertainty before increasing complexity** — never stack unknowns. **Search with purpose** — to answer a question, then stop. **Prefer evidence over intuition** — code, tests, logs, runtime behaviour. **Specify behaviour, not incidental detail** — preserve observable promises while leaving internals free to improve. **Keep momentum** — many small verified steps beat one large speculative change. **Replan when evidence changes** — plans guide execution, they do not constrain learning. **Stay in scope** — record adjacent improvements as new tasks. **Finish decisively** — completion means implemented, validated, and remaining risks recorded.

Clarity over cleverness. Completion over perfection.
