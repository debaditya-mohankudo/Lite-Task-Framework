---
name: task-verification
description: Standalone audit of test completeness — unit and integration — for a task. Checks every promised behaviour has a test that would catch its breakage, closes cheap gaps, names the rest. Run whenever there is implementation to audit, independent of finishing. Use when the user says /task-verification.
user-invocable: true
updated: 2026-08-25
doc: docs/methodology/05-verification.md
repo: ~/workspace/task-framework/.claude/skills/task-verification/skill.md
deployed: ~/.claude/skills/task-verification/skill.md
---

Implementation proves the code runs. This is about proving it is checked.

The reasoning lives in [05-verification.md](../../../docs/methodology/05-verification.md).

Its natural place is between implementation and introspection — after something exists to audit, before the retrospective grades it — but that is a recommended position, not a gate: it does not require task-implementation to have been invoked as a skill, run immediately after it, or complete before `tasks__finish`. It activates and deactivates its own task the same way grooming does, and can run whenever test completeness needs checking — while a task is still open, or well after it closed.

Run inside the larger create→groom→implement→finish→introspect loop, it forms a smaller loop of its own: a gap this pass finds and cannot close inline becomes a linked follow-up task rather than a private note (see Step 4), so it re-enters the same loop instead of sitting inert as an unresolved risk.

A green suite does not prove this pass is unnecessary — it proves the suite that already existed still passes, which says nothing about whether the *new* behaviour is what any of it is checking.

## Start

Use the supplied `task_id`. If none was given, read the active task instead of guessing.

```python
tasks__set_active(task_id)
tasks__log_skill_invocation(skill="task-verification/start", task_id=task_id)
tasks__context(task_id)
```

The checklist, `files`, and decisions are the audit surface. Auditing without pulling them is auditing from memory of what the task was supposed to do.

**Prerequisite: something must actually be implemented.** Check the bundle's `commits` and `resolution` before going further — if there are no commits and no checked-off resolution items, there is nothing to audit yet. Stop and say so rather than producing an audit of an empty task:

```
task:<id> has no implementation to verify — no commits, no checked-off checklist items. Run /task-implementation first.
```

A task with commits but an all-unchecked checklist is not disqualified by that alone — check items lag real progress (see [04-implementation.md](../../../docs/methodology/04-implementation.md)) — but commits with zero diff against the base, or a checklist that is still exactly as grooming left it, are the same signal and should stop the pass the same way.

## Step 1 — Find what actually changed

```python
tasks__log_skill_invocation(skill="task-verification/step-1-diff", task_id=task_id)
```

```bash
git log --grep "task:<id>" --oneline
git diff <base>..HEAD -- <files the commits touched>
```

Use the diff, not the task's `files` list — grooming's guess and implementation's actual footprint routinely disagree by the time a task finishes.

## Step 2 — Map promises to evidence

```python
tasks__log_skill_invocation(skill="task-verification/step-2-map-promises", task_id=task_id)
```

For each item in `resolution`, each stated boundary, and each failure behaviour logged as a decision, name the test that would go red if it broke. If none would, that is a gap — coverage-by-file is the wrong unit; a fully-executed file can still assert nothing about the behaviour that matters.

Weigh unit against integration per gap, not uniformly:

- Isolated logic, pure functions, edge cases in one component → unit.
- Anything crossing a process, a filesystem, a database, or another service's real behaviour → integration, wherever the cost of running it for real is small. A change that only has unit tests but touches a real boundary is still a gap.

**Prioritise silent-failure paths** — fail-open branches, logging-only code, idempotence, retried operations. Nothing else will ever surface a broken one of these; a loudly-failing path that lacks a test is a lower-priority gap than a silent one that lacks a test.

## Step 3 — Run the suite for real

```python
tasks__log_skill_invocation(skill="task-verification/step-3-run-suite", task_id=task_id)
```

```bash
.venv/bin/python -m pytest -q
```

Report pass/fail plainly. A finding built on an assumed-green suite is not a finding.

## Step 4 — Close what's cheap, name what isn't

```python
tasks__log_skill_invocation(skill="task-verification/step-4-close-or-name", task_id=task_id)
```

Add the missing test now if it's cheap — most gaps are. If closing a gap required a real choice (mocked vs. real boundary, what the test actually pins), log it:

```python
tasks__add_decision(task_id, "Added an integration test against a real temp "
                             "repo for X rather than mocking subprocess, "
                             "because the mock could not see Y.")
```

If a gap needs real test infrastructure that doesn't exist yet, don't hand-roll it inline. If closing it is real, non-trivial work, create it as a linked follow-up task instead of a note — `tasks__create` + `tasks__link` — so it re-enters create→groom→implement rather than sitting as an unresolved line nobody owns. For a gap too small to warrant its own task, name it as a risk the same way introspection recommends a follow-up task over improvising one mid-retrospective. There is no dedicated field for these smaller findings; an unclosed gap is exactly the shape of a groomed risk, so it goes there and rides the same grading introspection already does for everything else:

```python
tasks__update(task_id, grooming={
    **existing_grooming,
    "risks": [
        *existing_grooming.get("risks", []),
        {"text": "No test exercises the fail-open path in X — a broken "
                  "retry would ship silently.", "graded": None},
    ],
})
```

A closed gap needs no risk entry — the new test speaks for it.

## Step 5 — Report and clear

```python
tasks__log_skill_invocation(skill="task-verification/step-5-report", task_id=task_id)
tasks__clear_active()
```

```
## Verification: <id> — <title>

Suite            ✓ N passed / ✗ M failed / K skipped
Gaps closed      - added unit test for X
                 - pinned the fail-open path in Y
Gaps named       - <risk text>, logged to grooming.risks
                 - <gap>, spun out as linked task:<new-id>
Change-detectors - <test> asserts implementation, not contract — reworded / flagged
Ready to finish  yes / no — <what's blocking>
```

If every promise already had real coverage, say so in one line rather than inventing a gap to fill the shape.

## Rules

- **Implementation is a prerequisite, not an assumption.** Confirm commits or checked-off checklist items exist before auditing anything. No evidence of real work means there is nothing to verify yet — stop and say so.
- **Contract, not implementation.** A test that would need to change on a pure refactor with no behaviour change is a change-detector, not verification. Reword it to assert the promise, or flag it.
- **Coverage-by-file is not the question.** Ask which test would fail if a specific promise broke.
- **Silent-failure paths first.** They are the only gaps nothing else will ever catch.
- **Close cheap gaps now; name expensive ones.** Never invent test scaffolding mid-pass to fill the shape of "done."
- **Expensive gaps become the smaller loop.** Real follow-up work is a linked task (`tasks__create` + `tasks__link`), not a note that ends the trail — that is what keeps a verification failure inside the loop instead of outside it.
- **No new schema field.** Findings live in `grooming.risks` (gaps too small for their own task), `tasks__link` (gaps that became one), or `tasks__add_decision` (choices made while closing one) — all already exist for exactly this.
- **This pass does not finish the task.** `tasks__finish` is a separate, independent call — this skill only reports whether the work looks ready for it.
