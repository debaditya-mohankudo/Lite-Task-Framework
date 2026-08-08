---
name: task-grooming
description: Pre-implementation grooming pass. Removes uncertainty before implementation by pulling context, verifying premises, and recording falsifiable risks. Invoke with /task-grooming, /task-grooming task:<id>, or /task-grooming epic:<id>.
user-invocable: true
updated: 2026-08-08
doc: docs/methodology/03-grooming.md
repo: ~/workspace/task-framework/.claude/skills/task-grooming/skill.md
deployed: ~/.claude/skills/task-grooming/skill.md
---

Grooming is not for making a task prettier. It is for **removing uncertainty before implementation**, so coding can begin without another planning pause.

The reasoning lives in [03-grooming.md](../../../docs/methodology/03-grooming.md). This file is the operational pass.

Everything below is in service of one question:

> **Do we understand this task, the existing behaviour, consequences, unknowns, risks, and complexity well enough to make an informed implementation decision?**

Grooming is done when the answer is yes — not when every field has been filled in. A "no" is a legitimate outcome too, as long as what's blocking a "yes" is named. Use judgement about what "well enough" requires for this task; a one-line config change and a cross-service behaviour change do not need the same amount of digging.

## Input resolution

| Invocation | Action |
|---|---|
| `/task-grooming` | `tasks__list()`, ask which to groom |
| `/task-grooming task:<id>` | Groom one task |
| `/task-grooming epic:<id>` | Groom the open children — `tasks__list(parent="<epic id>")` |
| `/task-grooming task:<a> task:<b>` | Groom the explicit list |

## Step 0 — Log the invocation

```python
tasks__log_skill_invocation(skill="task-grooming/step-0-log-invocation", task_id=task_id)
```

One call, before Step 1. If grooming a batch, log once per task_id in the loop.

## Step 1 — Pull the context

```python
tasks__log_skill_invocation(skill="task-grooming/step-1-pull-context", task_id=task_id)
tasks__context(task_id)
```

Nothing is injected, so this call is not optional and there is no activation step that substitutes for it. Reading the task alone is not grooming — the graph and the neighbours are most of the signal.

The bundle returns the task, decisions, prior grooming, parent/children/edges, commits, and full-text neighbours. If `truncated` is present, sections were dropped for space — pull them individually rather than assuming they were empty.

**Batching is not a problem here.** There is no per-task activation cost and nothing to wait a turn for, so grooming twenty tasks is twenty `tasks__context` calls in a loop. No batch-size escape hatch is needed, and none exists — if grooming a large epic ever feels like it needs one, something else has gone wrong.

## Step 2 — Treat prior grooming as a draft

```python
tasks__log_skill_invocation(skill="task-grooming/step-2-treat-prior-grooming", task_id=task_id)
```

The bundle's `grooming` section holds the last pass. **Revise it; do not start from a blank page.** Carry forward what still holds, revise what changed, drop what is resolved, add what is new.

A risk already graded `avoided` or `materialized` is evidence about what actually held up — the most expensive information in the task. Discarding it to write a fresh block throws that away and risks re-flagging something already settled.

## Step 3 — Investigate

```python
tasks__log_skill_invocation(skill="task-grooming/step-3-investigate", task_id=task_id)
```

Do whatever it takes to answer the guiding question — no fixed checklist, because the right investigation depends on the task. Tools commonly worth reaching for:

```python
concept__list(repo="<abs repo path>")
concept__search(repo="<abs repo path>", query="<module or idea>")
concept__get(repo="<abs repo path>", name="<slug>")
concept__uncovered(repo="<abs repo path>", modules=["<file>", "..."])
```

`repo` is always required for concept tools; skip silently if the repo has no store. Anything a concept surfaces (an invariant, a contract, a gap no concept covers) goes in `prior_art` with the slug.

For every claim the task's plan rests on — "X is the authoritative file", "Y calls Z", "this is the production path", "this duplication is accidental" — **spend one concrete verification step before accepting it.** Read the file. Grep for callers. Check `git log`. Inspect the running system. This applies as much to a premise you wrote yourself an hour ago as to one you inherited — self-authored premises are exactly as unexamined and feel more trustworthy, which makes them worse.

When a claim is contested enough that a future reader would need to trust it without re-checking, say plainly how well-supported it is: `fact` (read and confirmed), `inference` (implied but not stated), `assumption` (believed, unverified), or `unknown` (flagged, not yet checked). Most claims don't need the label spelled out — only the ones where the distinction changes what happens next.

Consult the task graph rather than search hits alone when judging whether this is a duplicate, an orphan, or blocked on something else. If another task owns the same work, consolidate to one owner and link the rest with `tasks__link(..., rel="duplicates")` — but don't abandon a task unilaterally; surface it to the user first.

## Step 4 — Write findings

```python
tasks__log_skill_invocation(skill="task-grooming/step-4-write-findings", task_id=task_id)
tasks__update(task_id, grooming={
    "clarifications":         ["..."],
    "hidden_assumptions":     ["..."],
    "open_questions":         [{"question": "...", "blocking": False}],
    "risks":                  [{"text": "...", "graded": None}],
    "prior_art":              ["..."],
    "suggested_improvements": ["..."],
})
```

`hidden_assumptions` is something believed and left unverified. `open_questions` is something known to be unknown, with `blocking: true` when work cannot start without an answer. Only add an entry where the distinction changes what happens next — a task with no genuine open question doesn't need an empty list.

`grooming` replaces wholesale — only the latest pass is kept. That is about **storage, not authorship**: the object you pass should be the edited revision from Step 2. If a prior finding is worth keeping as history rather than as a live finding, put it in `prior_art`.

Fixing the checklist, `files`, or `motivation` itself is fine and encouraged — that is repair, not annotation. Findings go in `grooming`; repairs go in the fields they repair.

There is no `mark_groomed` flag and no `groomed_at` column. A task is groomed when its `grooming` is non-empty — the presence of the findings *is* the signal, so there is nothing to keep in sync.

## Risks must be falsifiable

Introspection grades every risk, so a risk that cannot be graded is noise.

> ✅ "Choosing the storage format will stall this — JSON column versus normalised tables is unresolved and the query patterns are unknown."
>
> ❌ "There may be unknowns."

The first can be graded `materialized`, `avoided`, or `wrong`. The second cannot be graded at all, and so teaches nothing. `tasks__grooming_accuracy` counts the ungradeable ones against you.

## Step 5 — Report

```python
tasks__log_skill_invocation(skill="task-grooming/step-5-report", task_id=task_id)
```

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
