---
name: task-introspection
description: Post-task retrospective that improves the engineering system. Review completed work, capture unlogged decisions, identify surprises, evolve memories, concepts, skills, and workflows so future executions become easier. Use when the user says /task-introspection or "retrospect on task:<id>".
user-invocable: true
updated: 2026-07-14
repo: ~/workspace/claude-hooks/skills/task-introspection/skill.md
deployed: ~/.claude/skills/task-introspection/skill.md
---

## Purpose

The purpose of introspection is **not** to remember the past.

The purpose is to make the **next execution better**.

Every completed task generates evidence. Use that evidence to improve:

* memories
* concepts
* skills
* documentation
* tooling
* automation
* engineering workflow

A successful introspection leaves the system slightly more capable than before.

---

## When to invoke

* User runs `/task-introspection` or `/task-introspection task:<id>`
* After `task:<id> done` or `tasks__finish` — can be chained immediately
* User asks: "retrospect", "what did we learn", "encode learnings"

---

## Step 1 — Identify the task

If a task id is supplied, use it. Otherwise use the most recently completed task.

```python
mcp__claude-hooks__tasks__get(id="<task_id>")
mcp__claude-hooks__tasks__history(id="<task_id>")
```

Review: title, original task, resolution, decisions, files changed, turn history, completion summary.

**If the task has no turn history (was never activated),** note that and skip straight to Step 3's decision/surprise questions using only the body diff — there's no turn-by-turn evidence to mine.

---

## Step 2 — Gather context

**Index first, then query.** Introspection typically runs minutes after the task's `/gc` commits landed — before any scheduled re-index — so a stale diff-RAG index silently returns nothing and starves Steps 3–4 of evidence:

```python
mcp__claude-hooks__diff_rag__index_commits(repo="<task's repo>")  # cheap — incremental from last indexed commit
mcp__claude-hooks__diff_rag__query(query="task:<task_id>", repo="<task's repo>")
mcp__claude-hooks__memory__search(query="<key concepts from task title/files>")
```

For cross-worktree setups, index the worktree where `/gc` actually committed (e.g. claude-hooks-dev, not test/main).

**Fallback if diff_rag returns nothing:** `git log --grep "task:<task_id>" --oneline -p --max-count=5` via Bash — the commits exist even when the index misses them. If that's also empty (task was never active at commit time, so `/gc` never tagged the commits), last resort: `git log --since="<task created_at>" -- <paths from the task's Files: section>`. Never let this step come up silently empty.

This context is used to understand what changed and what the system now knows — not to pad the report.

---

## Step 3.0 — Grade the grooming

This is the feedback loop that tells us whether grooming works — without it, grooming's predictions are write-only.

Look for `## Grooming Notes (YYYY-MM-DD)` sections in the task body (grade the most recent; mention older ones only if they contradict it). **If the task was never groomed, skip this step silently** — same escape hatch as Step 1's no-turn-history case.

For each item under **Risks**, **Hidden Assumptions**, and any "most likely to stall" prediction, grade it against what actually happened:

* **materialized** — predicted and it happened (did the recorded mitigation hold?)
* **avoided** — predicted, and the prediction caused the plan change that dodged it
* **wrong** — predicted but irrelevant; noise in the grooming pass
* **missed** — a Step 3.3 surprise that no grooming item anticipated

Grade prose as prose — LLM judgment over the markdown bullets, no parser, no structured schema. Summarize as one line for the Step 7 report:

```
Grooming accuracy: N predicted — M materialized, K avoided, J wrong; S surprises missed
```

Recurring `wrong` or `missed` classes feed Step 6: they're evidence the /task-grooming skill itself needs a better question.

---

## Step 3 — Engineering retrospective

Think like a senior engineer. The goal is not to summarize the task — it's to improve the engineering system. Work through each question, answering from task context; don't ask the user unless genuinely unclear.

### 1. Where did uncertainty come from?
Identify what consumed the most engineering effort (architecture, requirements, debugging, testing, deployment, tooling, hidden dependencies). Ask: *could this uncertainty have been removed before implementation?* If yes, recommend how — usually this means "the grooming pass should have caught X."

### 2. What decisions were made but never recorded?
**This is the highest-value part of introspection — never skip it.** Compare the original task, logged `## Task decisions`, turn history, and the actual implementation. For each missing decision:
```python
mcp__claude-hooks__tasks__add_decision(task_id="<id>", decision="<text>", session_id="<sid>")
```
**Never guess the session_id.** Read it from the `## Turn state` system-prompt block when visible. If it isn't visible, use `mcp__claude-hooks__hooks__session_id` rather than inventing one.

### 3. What surprised us?
Identify outcomes neither the task nor related context predicted — unexpected architecture, hidden coupling, framework behavior, API quirks, tool limitations, debugging discoveries. Ask: *should this become durable knowledge?* If yes, continue to Step 4.

### 4. What should exist next time?
Imagine another engineer starting the same task tomorrow — what should already exist? Memory, concept, documentation, task template, automation, MCP tool, workflow, reusable utility. Prefer improving the system over documenting history.

**If the improvement is a new tool or capability**, that's a code task, not something to improvise inline here — see [[skills-use-tools-not-implement-them]]. Recommend it as a follow-up task rather than hand-rolling a one-off script mid-introspection.

### 5. What became unnecessary?
Knowledge should evolve. Identify obsolete memories, stale documentation, outdated workflows, unnecessary process, superseded concepts. Recommend removal or update — **don't apply it unilaterally**: surface the specific item to the user ("memory `<slug>` looks superseded by this task — remove it, or keep for reference?") the same way a duplicate/orphan task gets flagged for confirmation in `/task-grooming`, rather than silently deleting or rewriting. Avoid accumulating stale knowledge, but don't let cleanup skip the user's say either.

---

## Step 4 — Memory evolution

Two capture channels, split by scope:

**Task-specific** (tied to this task's context — a design decision and rationale, a constraint or gotcha discovered here, a pattern that worked or failed here):

```python
mcp__claude-hooks__tasks__create_feedback(task_id="<id>", decision="...", constraint="...", pattern="...", session_id="<sid>")
```

All three fields optional — pass only what surfaced. This is the only place create_feedback is invoked since the finish-time retrospective template became a pointer to this skill (task:8c3c2ee4) — don't skip it when something task-specific surfaced.

**Globally reusable** (applies across tasks or domains — workflow discoveries, architectural patterns, debugging techniques, recurring pitfalls, framework behavior):

```python
mcp__claude-hooks__memory__add(name="<slug>", type="feedback", domain="<domain>", tags="..., task:<id>", body="...")
```

Include `task:<id>` as the last tag for traceability. Avoid recording task-specific trivia as global memories — that's what create_feedback is for. Link related memories with `[[slug]]` in the body where relevant — cheap now, saves a future search.

---

## Step 5 — Concept store review

Ask: *did this task change how the system should think about this domain?* If yes:

```
Skill(skill="update-concept-store", args="repo=<repo> touched_files=<files> context=<resolution and decisions>")
```

If no concept store exists, silently continue — don't note it as a gap. Only evolve concepts when the task genuinely changes domain understanding, not on every task.

**Regardless of whether concepts changed**, match the task's `Files:` (or a grooming pass's earlier `## Concept context` block, if present in the body) against the repo's concept store and record which concept slugs/names this task touched — this is bookkeeping, not evolution, so do it even when Step 5's update-concept-store call above was skipped. Detect format first (corrected 2026-07-24 — this step previously only checked `concept_store/concepts.json` and silently produced nothing for SQLite-format repos like SeniorDevAgent):

```bash
test -f "<repo>/concept_store/concepts.json" -a -f "<repo>/concept_store/store.py" && echo json
test -f "<repo>/concepts.db" -a -f "<repo>/concept_store.py" && echo sqlite
```

JSON format: match `Files:` against `concept["module"]` in `concept_store/concepts.json` — or, for a non-Java target repo, `concept__list(repo="<repo>")` (task:2813ece5) instead of reading the file directly. SQLite format: match `Files:` against each concept's evidence `source_ref` values (`ConceptStore.get_evidence(concept_id)`) or against `domain`/`name` for the touched subsystem — same lookup `/update-concept-store` Step 2b and `/task-grooming` Step 2 already use.

```
## Concepts touched
- hooks/gates.py: gates-prereq-chain-enforcement
```
(SQLite format: use the matched concept's `name`/`domain` in place of the JSON example's `module` slug.)

Fold this into the Step 7b persisted section rather than writing it separately. This gives future grooming/introspection passes a direct link from task → concept without re-deriving the file-to-concept match each time.

---

## Step 6 — System improvement review

Look beyond the task. Ask: *if this task were repeated tomorrow, what single improvement would save the most effort?* — better skill, automation, MCP tool, prompt, workflow, documentation, memory, concept, or task template.

Check Step 3.0's grades here: if the same class of grooming miss (`wrong` predictions or `missed` surprises) has now shown up across multiple introspections, recommend a specific /task-grooming update — a new Step 4 question, a new structural check, or sharper wording — rather than just noting the miss.

If a skill appears incomplete or wrong, recommend updating it:
```
Skill gap found: /task-framework step 2 doesn't mention X.
Update it? [yes/no]
```
Do not modify skills automatically unless the user confirms.

---

## Step 7 — Output

```text
## Introspection: task:<id> — <title>

Execution

✓ Smooth
⚠ Minor surprises
✗ Significant deviations

Grooming Accuracy

Grooming accuracy: N predicted — M materialized, K avoided, J wrong; S surprises missed
(omit section if the task was never groomed)

Major Sources of Uncertainty

- ...

New Decisions Captured

- ...

New Knowledge

- ...

Potentially Stale Knowledge

- ...

Recommended System Improvements

- ...

Highest-Leverage Improvement

- <single most valuable improvement>

Overall Assessment

<one concise paragraph>
```

Keep it brief and focused on insights, not chronology. If nothing to encode and everything went smoothly, say so in one line — don't pad. This is a 2-minute activity, not a report.

---

## Step 7b — Persist

Chat output evaporates; the task body rides the injection pipeline (`load_related_tasks` scores against title/body, and related-task snippets surface on future similar tasks). Persist the report, mirroring grooming's append convention:

1. `tasks__get(id="<task_id>")` — **`tasks__update(body=...)` REPLACES the body**, so fetch the existing body first; never pass only the new section.
2. Append the Step 7 report as a dated section — condensed to **≤10 lines, findings only** (Grooming Accuracy line, decisions captured, new/stale knowledge, highest-leverage improvement). Longer sections dilute the `body_snippet` future sessions actually see, and injected bodies are hard-truncated at 3000 chars.

```python
mcp__claude-hooks__tasks__update(id="<task_id>", body="<existing body>\n\n## Introspection (YYYY-MM-DD)\n...")
```

3. Re-index so the closed task's embedding includes the learnings:

```python
mcp__claude-hooks__tasks__index_task(task_id="<task_id>")
```

The task is already `done` at this point — `tasks__update` with only `body` does not touch status.

---

## Rules

- **Never skip decision-logging (Step 3.2).** It's the highest-value part of introspection.
- Don't ask the user to answer questions you can derive from task context.
- **Never guess the session_id.** Read from `## Turn state`, or use `hooks__session_id` if it isn't visible.
- **Never unilaterally delete or rewrite stale memories/skills/concepts.** Flag them and let the user decide — same standard as flagging a duplicate/orphan task in `/task-grooming` rather than closing it unasked.
- **New capabilities are code tasks, not inline scripts.** If Step 3.4 or Step 6 surfaces a missing tool, recommend building it as its own task rather than improvising it during the retrospective.
- Keep the output tight — one line per finding.

---

## Engineering philosophy

Every completed task is evidence. The objective is not to document everything — it's to improve the engineering system.

Ask yourself:

* What slowed us down?
* What surprised us?
* What assumptions proved wrong?
* What knowledge became durable?
* What knowledge became obsolete?
* What should change before another engineer performs this task?

Prefer improving the system over recording history. A successful introspection should make the next execution faster, more predictable, and less dependent on individual experience.
