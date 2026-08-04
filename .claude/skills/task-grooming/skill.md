---
name: task-grooming
description: Pre-implementation grooming pass. Removes uncertainty before implementation by pulling context, verifying premises, and recording falsifiable risks. Invoke with /task-grooming, /task-grooming task:<id>, or /task-grooming epic:<id>.
user-invocable: true
updated: 2026-08-01
doc: docs/methodology/03-grooming.md
repo: ~/workspace/task-framework/.claude/skills/task-grooming/skill.md
deployed: ~/.claude/skills/task-grooming/skill.md
---

Grooming is not for making a task prettier. It is for **removing uncertainty before implementation**, so coding can begin without another planning pause.

The reasoning lives in [03-grooming.md](../../../docs/methodology/03-grooming.md). This file is the operational pass.

After a grooming pass, an engineer should know what to build, where, why that way, what risks remain, and what success looks like.

## Input resolution

| Invocation | Action |
|---|---|
| `/task-grooming` | `tasks__list()`, ask which to groom |
| `/task-grooming task:<id>` | Groom one task |
| `/task-grooming epic:<id>` | Groom the open children — `tasks__list(parent="<epic id>")` |
| `/task-grooming task:<a> task:<b>` | Groom the explicit list |

## Step 0 — Log the invocation

```python
tasks__log_skill_invocation(skill="task-grooming", task_id=task_id)
```

One call, before Step 1. If grooming a batch, log once per task_id in the loop.

## Step 1 — Pull the context

```python
tasks__context(task_id)
```

Nothing is injected, so this call is not optional and there is no activation step that substitutes for it. Reading the task alone is not grooming — the graph and the neighbours are most of the signal.

The bundle returns the task, decisions, prior grooming, parent/children/edges, commits, and full-text neighbours. If `truncated` is present, sections were dropped for space — pull them individually rather than assuming they were empty.

**Batching is not a problem here.** There is no per-task activation cost and nothing to wait a turn for, so grooming twenty tasks is twenty `tasks__context` calls in a loop. No batch-size escape hatch is needed, and none exists — if grooming a large epic ever feels like it needs one, something else has gone wrong.

## Step 2 — Treat prior grooming as a draft

The bundle's `grooming` section holds the last pass. **Revise it; do not start from a blank page.** Carry forward what still holds, revise what changed, drop what is resolved, add what is new.

A risk already graded `avoided` or `materialized` is evidence about what actually held up — the most expensive information in the task. Discarding it to write a fresh block throws that away and risks re-flagging something already settled.

## Step 3 — Concept lookup

```python
concept__list(repo="<abs repo path>")
concept__search(repo="<abs repo path>", query="<module or idea>")
concept__get(repo="<abs repo path>", name="<slug>")
```

Match the task's `files` against each concept's `module`. `repo` is always required. Skip silently if the repo has no store.

For each match, check:

- **Invariant conflict** — does the plan violate something the concept asserts as always-true?
- **Contract break** — does the plan change what the module promises callers?
- **New concept** — does the task introduce behaviour no concept captures?

Also worth running when the task adds a module:

```python
concept__uncovered(repo="<abs repo path>", modules=["<file>", "..."])
```

Record findings in `prior_art` and note the slugs.

## Step 4 — Verify the premise

The highest-value step, and the easiest to skip.

For anything claiming "X is the authoritative file", "Y calls Z", "this is the production path", or "this duplication is accidental" — **spend one concrete verification step before accepting it.** Read the file. Grep for callers. Check `git log`. Inspect the running system.

This applies just as much to a premise you wrote yourself an hour ago as to one you inherited. Self-authored premises are exactly as unexamined and feel more trustworthy, which makes them worse.

## Step 5 — Engineering review

1. **Is the outcome obvious?** Would two engineers produce essentially the same implementation? If not, name the ambiguity.
2. **Can work start today?** If not, what decision or dependency is missing?
3. **What assumptions are hidden?** Architecture, data format, ordering, deployment. Validate what you can; record the rest.
4. **Does history change the plan?** Read the neighbours and the commits in the bundle.
5. **Is it a duplicate or an orphan?** Check against parent and siblings, not just search hits.
6. **Is it one session's work?** If not, split it.
7. **What will stall this?** Predict the largest remaining risk.

## Step 6 — Structural checks

| Check | Passes when |
|---|---|
| Resolution is a checklist | `resolution` has concrete items |
| File paths named | Items name a file or module |
| Dependencies recorded | Prerequisites exist as edges, not just prose |
| No duplicate ownership | No other task's checklist owns the same file edit |
| No blocking TBD | Nothing needed to start is unresolved |
| Progress matches status | Not every item ticked while status is still `open` |

Duplicate ownership is distinct from contradiction: two tasks can agree on what to do and still be a problem, because neither is the source of truth for when it is done. Consolidate to one owner and link the others with `tasks__link(..., rel="duplicates")`.

## Step 7 — Write findings

```python
tasks__update(task_id, grooming={
    "clarifications":         ["..."],
    "hidden_assumptions":     ["..."],
    "risks":                  [{"text": "...", "graded": None}],
    "prior_art":              ["..."],
    "suggested_improvements": ["..."],
})
```

`grooming` replaces wholesale — only the latest pass is kept. That is about **storage, not authorship**: the object you pass should be the edited revision from Step 2. If a prior finding is worth keeping as history rather than as a live finding, put it in `prior_art`.

Fixing the checklist, `files`, or `motivation` itself is fine and encouraged — that is repair, not annotation. Findings go in `grooming`; repairs go in the fields they repair.

There is no `mark_groomed` flag and no `groomed_at` column. A task is groomed when its `grooming` is non-empty — the presence of the findings *is* the signal, so there is nothing to keep in sync.

## Risks must be falsifiable

Introspection grades every risk, so a risk that cannot be graded is noise.

> ✅ "Choosing the storage format will stall this — JSON column versus normalised tables is unresolved and the query patterns are unknown."
>
> ❌ "There may be unknowns."

The first can be graded `materialized`, `avoided`, or `wrong`. The second cannot be graded at all, and so teaches nothing. `tasks__grooming_accuracy` counts the ungradeable ones against you.

## Step 8 — Report

```
✓ task:abc — ready  (title)
⚠ task:def — 2 gaps: file paths missing, decision needed: storage format
⚠ task:ghi — duplicates task:xyz on tools/db.py

N tasks groomed — M ready, K need updates.
```

## Rules

- **`tasks__context` is mandatory.** There is no activation that fetches it for you.
- **Grooming is not starting.** A groomed task is not an active one. Do not leave it half-implemented because grooming went well. There is no status to reset — grooming never changed it.
- **Revise the prior grooming; do not overwrite it blind.**
- **Every risk must be falsifiable.**
- **Do not abandon a task unilaterally.** If it looks like a duplicate or orphan worth abandoning, surface it to the user first.
- If nothing needs changing, still write the findings and say "ready as-is".
