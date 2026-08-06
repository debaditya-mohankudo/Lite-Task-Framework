---
name: task-introspection
description: Post-task retrospective that improves the engineering system. Grades grooming's predictions, captures unlogged decisions, and evolves concepts and skills so the next execution is cheaper. Use when the user says /task-introspection or "retrospect on task:<id>".
user-invocable: true
updated: 2026-08-06
doc: docs/methodology/05-introspection.md
repo: ~/workspace/task-framework/.claude/skills/task-introspection/skill.md
deployed: ~/.claude/skills/task-introspection/skill.md
---

The purpose is not to remember the past. It is to make the **next** execution better.

The reasoning lives in [05-introspection.md](../../../docs/methodology/05-introspection.md).

This is the step that closes the loop, and the easiest one to skip — the work is done, the tests pass, and moving on feels like progress. Skipping it is what makes grooming decorative, because ungraded predictions teach nothing.

## Step 1 — Identify the task

Use the supplied id, or the most recently finished task.

```python
tasks__log_skill_invocation(skill="task-introspection/step-1-identify-task", task_id=task_id)
tasks__context(task_id)
```

Everything needed is in the bundle: the task, decisions already logged, the grooming being graded, the graph, and the commits. There is no separate history call and no re-indexing step.

## Step 2 — Gather what the bundle does not hold

```python
tasks__log_skill_invocation(skill="task-introspection/step-2-gather-more", task_id=task_id)
```

The bundle's `commits` section lists what landed. To see the diffs, use git directly:

```bash
git log --grep "task:<id>" --oneline -p --max-count=5
```

If that is empty because commits were never tagged, fall back to `git log --since=<task created_at> -- <files from the task>`. Never let this step come up silently empty.

**Link any commit the bundle is missing.** `tasks__add_commit` is a manual, unhooked step — a commit made via a raw `git commit` (not through the /commit skill) never reaches it, so the bundle's `commits` section can be empty even when `git log --grep` just found a real, correctly-tagged commit. Diff the two: for every sha `git log --grep` surfaces that isn't already in the bundle's `commits`, call `tasks__add_commit(task_id, sha, repo)`. Cheap, idempotent, and this is the step that would have caught it — three tasks in one session shipped with silently-missing commit links before this was added (see loop memory: raw-git-commit-skips-tasks-add-commit).

The bundle also carries no operational trace — what the tools actually did while the task was active, as opposed to what was decided. `tasks__logs` fills that gap:

```python
tasks__logs(logger="taskfw.skill.<name>/<step-slug>", limit=50)   # one section's calls
tasks__logs(limit=200)                                            # everything, filter client-side by prefix
```

`logger` is exact-match only — there is no prefix query — so auditing a whole skill's run means either the exact name per section or an unfiltered pull filtered afterward.

Use Read and Grep for the code. The framework ships no code search of its own, deliberately.

## Step 3 — Grade the grooming

```python
tasks__log_skill_invocation(skill="task-introspection/step-3-grade-grooming", task_id=task_id)
```

**If the task has no `grooming`, skip this step silently.** There is no `groomed_at` flag to check — the presence of the findings is the signal.

For each item in `grooming.risks`, grade what actually happened:

- **materialized** — predicted, and it happened. Did the mitigation hold?
- **avoided** — predicted, and the prediction caused the change that dodged it.
- **wrong** — predicted, but irrelevant. Noise in that pass.
- **missed** — a surprise no prediction anticipated. This has no risk to grade against; it goes in `missed_surprises` at Step 6.

`hidden_assumptions` is **not** graded — those are things grooming identified in the present, not predictions about the future.

Write the grades back by re-passing the whole grooming object with `graded` filled in:

```python
tasks__update(task_id, grooming={
    **existing_grooming,
    "risks": [
        {"text": "<unchanged text>", "graded": "avoided"},
        {"text": "<unchanged text>", "graded": "wrong"},
    ],
})
```

**`grooming` replaces wholesale**, so pass the other keys back unchanged or they are lost. Keep each risk's `text` byte-identical — `tasks__grooming_accuracy` groups recurring risks by text, and a reworded risk stops matching its own history.

Be honest about *materialized* versus *unresolved*. A risk that said "decide X now" and was simply not decided has not materialized — it is still open, and grading it as inevitable launders a skipped decision into a recorded outcome. If grading surfaces a decision you skipped, **make it now**; that is the loop working.

## Step 4 — Grade the grader

```python
tasks__log_skill_invocation(skill="task-introspection/step-4-grade-the-grader", task_id=task_id)
tasks__grooming_accuracy(limit=25)
```

Run this every few tasks, not every time — a pattern needs a series. It is the only tool that reads across tasks, and it is what turns "repeated `wrong` grades" from an observation into something actionable.

Act on what comes back:

- **Low `predictive_value`** — grooming is generating noise. Ask fewer, sharper questions.
- **A recurring risk** — the same thing keeps threatening work. Fix the cause or add it to grooming's structural checks, rather than predicting it again.
- **`skipped_introspection`** — the loop is not running on some finished tasks. Nothing else matters until that is fixed.
- **`self_report_disagreements`** — a past report's tallies disagree with its own risk grades. The grades are authoritative; the summary drifted.

Both of the first two are signals to change grooming itself, not just to note the miss.

## Step 5 — Ask

```python
tasks__log_skill_invocation(skill="task-introspection/step-5-ask", task_id=task_id)
```

1. **Where did the uncertainty come from?** Could grooming have removed it?
2. **What decisions were never recorded?** Compare the plan to what was built. Log every gap with `tasks__add_decision` — **this is the highest-value part of the pass.**
3. **What surprised us?** Should it become durable knowledge?
4. **What should already exist next time?** Prefer improving the system over documenting history. If the improvement is a new capability, recommend it as a follow-up task rather than hand-rolling a script mid-retrospective.
5. **What became obsolete?** Flag it; do not delete it unilaterally.

## Step 6 — Record

```python
tasks__log_skill_invocation(skill="task-introspection/step-6-record", task_id=task_id)
tasks__add_introspection(task_id, report={
    "date": "YYYY-MM-DD",
    "grooming_accuracy": {"predicted": N, "materialized": M, "avoided": K, "wrong": J},
    "missed_surprises": ["..."],
    "new_knowledge": ["..."],
    "stale_knowledge_flagged": ["..."],
    "highest_leverage": "The single most valuable improvement.",
    "overall_assessment": "One honest paragraph.",
})
```

Reports **append**, unlike grooming. Each is evidence about a distinct execution, and the series is worth more than any one entry.

The `grooming_accuracy` tallies here are a human-readable summary. They are **not** what `tasks__grooming_accuracy` counts — that recomputes from the per-risk grades and reports any disagreement with what you wrote here. Get them right, but the grades are the record.

## Step 7 — Loop memory

```python
tasks__log_skill_invocation(skill="task-introspection/step-7-loop-memory", task_id=task_id)
```

Three of the four things a good pass produces already have homes: graded risks in the task's grooming, decisions in `tasks__add_decision`, architectural facts in the concept store. The other two belong nowhere else.

```python
task_memory__record(slug="degrade-fts-to-like", task_id="<id>", kind="constraint",
                    text="FTS5 is a compile-time option, so search must degrade rather than fail.")
```

`kind` is `constraint` (learned the hard way), `technique` (worth reusing), or `pitfall` (keeps recurring — a repeated `wrong` or `missed` grade from Step 4 is usually one of these). The task id is required.

**Scope discipline.** This is memory about the loop, not a general knowledge store. If the lesson is about a module, it is a concept. If it is about this task, it is a decision. Only record here what is neither.

Grade what is already there. A memory claims a lesson generalises, and this task is evidence for or against:

```python
task_memory__recall(query="<the area this task touched>")
task_memory__link(slug="...", task_id="<id>", relation="confirmed_by")     # it held again
task_memory__link(slug="...", task_id="<id>", relation="contradicted_by")  # it did not
```

Standing is derived from those links, so it cannot drift from its evidence. A `disputed` memory is reported as disputed — resolve it by judgment, not by re-recording over it.

When a lesson is superseded rather than wrong:

```python
task_memory__supersede(slug="<old>", by="<new>")
```

The row survives and names its replacement — the same standard as flagging stale knowledge rather than deleting it. Reserve `task_memory__forget` for a lesson that should never have been recorded.

## Step 8 — Concept store

```python
tasks__log_skill_invocation(skill="task-introspection/step-8-concept-store", task_id=task_id)
```

The store is a live body meant to grow, not just get corrected. A task leaves behind either a **change** (an existing concept was wrong or incomplete) or **growth** (a module had no concept and this task is the first to understand it well enough to write one).

```python
concept__list(repo="<abs repo path>")
concept__uncovered(repo="<abs repo path>", modules=["<files this task touched>"])
```

For a module whose concept changed, or a module this task covered first:

```python
concept__upsert(repo="<abs repo path>", concept={
    "name": "<kebab-slug>",
    "module": "<file>",
    "description": "...",
    "contracts": ["..."],
    "invariants": ["..."],
    "evidence": ["<file>:<function>", "tests/<file>:<class>"],
})
```

`upsert` merges, so updating one field cannot silently drop invariants you did not mention. An explicit empty list still clears a field — deletion stays possible, it just has to be deliberate.

Skip where the change was test-only or doc-only with no new behaviour to capture. Skip silently if the repo has no store.

## Step 9 — Output

```python
tasks__log_skill_invocation(skill="task-introspection/step-9-output", task_id=task_id)
```

```text
## Introspection: <id> — <title>

Execution         ✓ smooth / ⚠ minor surprises / ✗ significant deviations
Grooming accuracy  N predicted — M materialized, K avoided, J wrong; S missed
                   (omit if never groomed)
Uncertainty        - ...
Decisions captured - ...
New knowledge      - ...
Possibly stale     - ...
Improvements       - ...
Highest leverage   - <the single most valuable one>
Overall            <one honest paragraph>
```

**Keep it short.** This is a two-minute activity, not a report. One line per finding. If nothing surprised you and everything went smoothly, say exactly that in one line — padding teaches the next reader to skim.

## What good looks like

A useful pass produces one of: a graded risk that changes how the next task is groomed; a decision that was made but never written down; a constraint discovered the hard way, now recorded; or a named technique worth reaching for again.

If a pass produces none of those, it was probably not worth running — say so rather than inventing findings to fill the shape.

## Rules

- **Never skip decision-logging.** It is the highest-value part of the pass.
- **Keep risk text byte-identical when grading**, or recurrence tracking silently breaks.
- **Pass the whole grooming object back** — it replaces, it does not merge.
- **Never unilaterally delete stale memories, skills, or concepts.** Flag them; let the user decide.
- **New capabilities are code tasks, not inline scripts.**
- Do not ask the user what you can derive from the task.
