# 5. Verification

Implementation proves the code runs. This document is about proving it is
**checked** — that for every promise the task made, something would fail if
the promise stopped holding.

A green suite is not evidence of this by itself. A suite that never exercised
the changed behaviour passes just as loudly as one that did. Verification is
the pass that tells the difference before introspection has to.

## The guiding question

> For every behaviour this task promised — the checklist, the stated
> boundaries, the failure paths — is there a test that would fail if it broke?
> At the level that would actually catch the break: unit where the change is
> isolated, integration where it crosses a real boundary.

Verification is done when the answer is yes for everything that shipped, or
when every gap has been named. "The tests pass" answers a different question
and is not a substitute.

## Start

Use the supplied `task_id`. If none was given, use the active task instead of
guessing.

```python
tasks__context(task_id)
```

The checklist, `files`, and any decisions are the audit surface. Reading them
here is not optional — auditing without them is auditing from memory of what
the task was supposed to do, which is exactly the assumption verification
exists to check.

## Establish what actually changed

Read the diff, not the plan. A task's `files` list is what grooming expected
to touch; the diff is what implementation actually touched, and the two
routinely disagree by the time a task finishes.

```bash
git log --grep "task:<id>" --oneline
git diff <base>..HEAD -- <files the commits touched>
```

Every changed line is either covered by an existing test, covered by a test
this pass adds, or a named gap. There is no fourth outcome.

## Map promises to evidence, not files to tests

Coverage-by-file is the wrong unit. The question is whether each *promise*
has a test that would catch its breakage — a file can be 100% executed by
tests that assert nothing about the behaviour that matters, and a single test
can be the only thing standing behind three separate checklist items.

For each item in the task's `resolution`, each stated boundary, and each
failure behaviour recorded during implementation, ask which existing test
would go red if it broke. If none would, that is the gap — not "this file has
no tests."

## Unit and integration are different questions

A unit test proves the change is locally correct in isolation. An integration
test proves it survives contact with the real boundary — a real subprocess, a
real temporary database, a live round trip through the actual tool surface
rather than a mock of it. They fail differently and neither substitutes for
the other:

- Isolated logic, pure functions, edge cases in one component → unit.
- Anything crossing a process, a filesystem, a database, or another service's
  real behaviour → integration, where the cost of running it for real is
  small enough to afford. [04-implementation.md](04-implementation.md) already
  says to verify against reality where that cost is small — this pass is
  where that guidance gets checked, not just trusted.

A task that only has unit tests for a change that touches a real boundary has
a gap, even if every unit test is green.

## Silent failure is the highest-priority gap

Test what produces no error when it breaks — fail-open paths, logging-only
branches, idempotence, retried operations that should not double-apply. These
are the paths [04-implementation.md](04-implementation.md) already flags as
needing tests; verification is where a missed one is actually caught, because
nothing else will ever surface it. Prioritise these over adding coverage to
paths that already fail loudly on their own.

## Contract, not implementation

A test that would need to change if the internals were refactored but the
behaviour stayed the same is a change-detector, not a regression test. Reread
each test this task added or touched and ask what promise it is actually
pinning. If the answer is "whatever the code currently does," it is not
verification — it is a snapshot.

## Run it for real

```bash
.venv/bin/python -m pytest -q
```

Report the result plainly — pass count, fail count, anything skipped and why.
A verification pass that does not actually run the suite is reporting an
assumption, not a finding.

## Close the gaps that are cheap; name the ones that are not

A missing test for a silent-failure path or an untested checklist item is
usually cheap to add now — add it. A gap that would require substantial new
test infrastructure is not something to build inline; name it instead, the
same way introspection prefers recommending a follow-up task over hand-rolling
a script mid-retrospective.

There is no dedicated field for verification findings — the schema does not
need one. A real gap is exactly the shape of a groomed risk, so it belongs in
`grooming.risks`, gradable by the same introspection pass that already grades
every other risk:

```python
tasks__update(task_id, grooming={
    **existing_grooming,
    "risks": [
        *existing_grooming.get("risks", []),
        {"text": "No test exercises the fail-open path in X — a broken retry "
                  "would ship silently.", "graded": None},
    ],
})
```

A gap that was closed during this pass does not need a risk — the test now
speaks for it. Log a decision instead if closing it required a real choice:

```python
tasks__add_decision(task_id, "Added an integration test hitting a real temp "
                             "repo for X rather than mocking subprocess, "
                             "because the mock could not see Y.")
```

## What good looks like

A useful pass produces one of: a test added for a promise that had none, a
silent-failure path pinned for the first time, a change-detector replaced
with a real contract test, or a named gap too large to close inline. If every
promise already had real coverage, say that in one line — inventing a gap to
fill the shape is worse than reporting none.

## Finish

Verification does not close the task — that is still `tasks__finish` in
implementation's hands. This pass only decides whether what is about to be
finished is actually checked, and records what it found.
