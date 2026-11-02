# 4. Implementation

Grooming removed the uncertainty. This document is about not reintroducing it.

## Start

Use the supplied `task_id`. If none was given, read the active task instead
of guessing. The active pointer is a single value, not a stack: setting it
replaces whatever was active, with no confirmation, and nothing restores the
displaced task later. If you are stepping away from another task, that is the
moment to note where you left it.

```python
tasks__set_active(task_id)
tasks__context(task_id)     # read the grooming findings before writing code
```

The grooming findings are the point. Reading the title and starting to type
discards the pass that was just paid for.

## Specify before implementing, when the output shape is the hard part

If a story's difficulty is *what a thing returns* rather than how it works,
write the contract in prose first — sections, ordering, limits, failure
behaviour — then implement against it.

This turns coding into transcription: no design happens mid-implementation, and
tests get written against the stated contract rather than against whatever the
code turned out to do. It is the single highest-leverage habit in this loop.

## Be behavioural, not detail-oriented

Lead with what a user or caller can observe: the outcome, boundaries, failure
behaviour, and invariants that must hold. Implementation details are a means to
that end, not the contract. Prefer guidance and tests that leave room for a
better internal design while pinning the behaviour that matters.

When deciding whether to add detail, ask whether it changes an observable
promise. If it does not, keep it flexible unless it is needed to make the work
safe, understandable, or consistent with an established convention.

## Log decisions as they happen

```python
tasks__add_decision(task_id, "Chose X over Y because Z.")
```

Not at the end — at the moment. A decision recorded later is reconstructed, and
reconstruction quietly rewrites the reasoning into whatever now seems sensible.

Log a decision when you chose between real alternatives, when you rejected the
obvious approach, when a constraint forced your hand, or when you did something
that will look wrong to someone who was not there.

## Prefer removing a failure mode over policing it

The recurring technique in this codebase, and worth reaching for first:

- A value derived on read cannot drift from its source, so no reconciliation
  logic is needed.
- A migration function containing no destructive code path cannot perform a
  destructive migration, so no review vigilance is needed.
- A typed field cannot be a malformed section, so no body validator is needed.

Before writing a check, ask whether the thing being checked needs to be
representable at all. Deleting the failure mode is always cheaper than
detecting it.

## When something surprises you

Stop and write it down before continuing. A surprise mid-task is the cheapest
knowledge you will get all day, and it evaporates within the hour. It belongs
in a decision, in `notes`, or in the introspection report.

## Check off deliverables as they land

```python
tasks__check_item(task_id, index=..., done=True)
```

The moment an item is actually done — not batched right before
`tasks__finish`. A task whose real work landed but whose checklist still
reads all-unchecked is indistinguishable from the outside from a task with no
progress: "done" and "empty" look the same until someone actually reads it.

If an item can't be checked off — a decision is still open, or what it asked
for changed mid-task — say so. Leave it unchecked and log why with
`tasks__add_decision` or in `notes`. An unresolved item with no explanation
reads as forgotten; one with a stated reason reads as a deliberate open
question.

## Tests

Test the contract, not the implementation. A test that asserts what the code
already does is a change-detector; a test that asserts the promise is a
regression test.

Test what fails silently — logging, fail-open paths, idempotence. Anything
whose failure produces no error is exactly what needs a test, because nothing
else will ever tell you it broke.

Verify against reality where the cost is small. A real temporary git repository
catches things a mocked subprocess cannot, and a live end-to-end run catches
things assertions do not.

## When a test fails, decide which side is wrong

Sometimes the test encodes an assumption the design deliberately contradicts.
Do not reflexively change the implementation to satisfy it. Ask which one
expresses the intended behaviour, then fix the other — and if the design was
right, add a test that pins the interaction explicitly so the next person does
not have the same argument.

## Read the logs before you close

A tool result is the tool's own account of itself. The operational log is a
second witness, and the one dimension a green suite cannot show: what the
tools actually did while you worked.

Read it for three things. A failed call you may have carried on from, as if
its result existed. A refusal by a rule that was read as a no-op. And any
measured gap nobody reads automatically — an observable with no reader
teaches nothing unless someone looks.

This exists because of a call that returned success and did nothing
(task:1f400ecf). The running server advertised an older schema, so a
parameter was silently dropped before it reached the code. The tool
truthfully reported success at what it had been asked, which was less than
the caller asked. Nothing failed and nothing logged an error; only the store's
unchanged state disagreed. Introspection reads the logs too, but reading them
here is reading them while you can still act on what they say.

## Commit, and link the commit

Every commit cites `task:<id>`: the commit→task edge is how `tasks__context`
reassembles what happened later, and an uncited commit is invisible to the loop
that is supposed to learn from it. Never cite a task the work does not belong
to — a false edge is worse than a missing one, because a missing one is
visible as missing.

Citing is not linking. Nothing watches git and records the edge; after the
commit, call `tasks__add_commit` yourself. A pre-commit gate would make this a
rule, and it deliberately is not one — so it only exists if you make it.
Missed links can be rebuilt from git history (`python -m taskfw.backfill`),
which keeps the record recoverable, but that is the recovery path, not a
substitute.

## Finish

```python
tasks__finish(task_id, reason="what actually shipped")
```

Finish after the work is committed and linked, so the task closes on what
actually landed. The checklist should already be checked off by now (see
above) — this call closes the task, it does not reconcile it.

State outcomes plainly. If tests fail, say so. If a step was skipped, say that.
A `reason` that overstates what landed is worse than none, because it will be
believed.
