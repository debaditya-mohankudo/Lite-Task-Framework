---
name: task-grooming
description: Pre-implementation grooming pass. Reduce uncertainty before implementation by activating the task, gathering related context, identifying hidden assumptions, and improving task readiness. Use before starting a task or sprint. Invoke with /task-grooming, /task-grooming task:<id>, or /task-grooming epic:<id>.
user-invocable: true
updated: 2026-07-14
repo: ~/workspace/claude-hooks/skills/task-grooming/skill.md
deployed: ~/.claude/skills/task-grooming/skill.md
---

## Purpose

The purpose of grooming is **not** to make a task prettier.

The purpose of grooming is to remove uncertainty before implementation.

After grooming, an engineer should know:

* **What** to build.
* **Where** to build it.
* **Why** it should be built this way.
* **What risks remain.**
* **What success looks like.**

A well-groomed task should allow implementation to begin immediately without another planning pause.

---

## When to invoke

* Before starting implementation.
* Before activating a task for development.
* After creating an epic and its subtasks.
* Whenever a task has significantly changed in scope.

---

## Input resolution

| Invocation | Action |
|---|---|
| `/task-grooming` | List open tasks and ask which to groom |
| `/task-grooming task:<id>` | Groom one task |
| `/task-grooming epic:<id>` | Groom all open/blocked children of that epic |
| `/task-grooming task:<id1> task:<id2>` | Groom explicit list |

```python
# Single task
mcp__claude-hooks__tasks__get(id="<id>")

# Children of an epic
mcp__claude-hooks__tasks__list()  # filter by parent_id == epic_id
```

If no argument given, call `tasks__list()` and ask which tasks to groom.

---

## Step 1 — Activate and read context

```python
mcp__claude-hooks__tasks__set_active(task_id="<id>", session_id="<session_id>")
```

**Never guess the session_id.** Read it from the `## Turn state` system-prompt block when visible. If it isn't visible, use `mcp__claude-hooks__hooks__session_id` (built-in retry, no active task required) rather than inventing one.

Activation is mandatory because it retrieves:

* Active task (body, decisions)
* Related tasks (top-3 semantically similar)
* Related commits (top-3 diff hunks)
* Code RAG (top-3 modules)
* Concept store matches, if the repo has one (see Step 2)

These are the primary inputs to grooming — reading the body in isolation without activating is not grooming.

**Grooming a large batch (>5–10 tasks, e.g. a big epic):** the literal per-task activate → wait-a-turn → read-injected-context loop doesn't scale — each activation's related-context only lands on the *next* turn, so 20+ tasks means 20+ turns. When batch size crosses that threshold, it's acceptable to substitute direct lookups for equivalent signal instead: `tasks__get` on all candidates up front, `tasks__neighbors`/`diff_rag__query`/`code_rag__smart_search` called directly rather than waiting for injection, and grepping the actual repo for files named in each task's `Files:` section to verify claims. State plainly in the report that this substitution was made and why — it's a disclosed deviation, not silent corner-cutting.

---

## Step 2 — Concept store lookup (if the repo has one)

Two formats are in active use (corrected 2026-07-24 — this step previously only checked the JSON format and silently skipped every SQLite-format repo, e.g. SeniorDevAgent, treating "wrong format" as "no store exists"). Detect first, same check `/update-concept-store` uses:

```bash
test -f "<repo>/concept_store/concepts.json" -a -f "<repo>/concept_store/store.py" && echo json
test -f "<repo>/concepts.db" -a -f "<repo>/concept_store.py" && echo sqlite
```

**JSON format** (claude-hooks-dev pattern):

```python
import json
from pathlib import Path
concepts = json.loads(Path("<repo>/concept_store/concepts.json").read_text())
```

Or, for a non-Java target repo (not claude-hooks-dev's own store), `concept__list(repo="<repo>")`/`concept__get(repo="<repo>", name=...)` (task:2813ece5, added 2026-07-24) do the same read without hand-writing this snippet each time — prefer these when grooming a task in a repo other than claude-hooks-dev itself.

Prefer a `Concepts:` section in the task body if present — look those slugs up directly. Otherwise match the task's `Files:` section against `concept["module"]`.

**SQLite format** (SeniorDevAgent pattern):

```python
from concept_store import ConceptStore
store = ConceptStore("<repo>/concepts.db")
concepts = store.list_concepts()  # then match by domain, or by evidence source_ref against Files:
```

Match the task's `Files:` section against each concept's evidence (`store.get_evidence(concept_id)`'s `source_ref` values) or against `domain`/`name` for the subsystem a touched file belongs to — same matching logic `/update-concept-store` Step 2b already uses.

For each match, in either format, check:

* **Invariant conflict** — does the task's plan violate something the concept's description asserts as always-true for that module? (SQLite concepts don't have a separate `invariants` field — read this out of `description`, and check `risk_findings`/`test_gaps` via `get_risk_findings`/`get_test_gaps` for already-known open concerns the plan should account for.)
* **Contract break** — does the plan change what the module promises callers?
* **New concept** — does the task introduce behavior not captured by any existing concept?

Append matches as a `## Concept context` block in the grooming notes:

```
## Concept context
- hooks/gates.py: gates-prereq-chain-enforcement
  invariants: ["Gates fail open on DB errors", "External gates never override internal ones"]
  → check: does this task's change respect these invariants?
```

(SQLite format: substitute the concept's `name`/`domain` and relevant `description`/open risk_findings for the JSON example's `module`/`invariants`.)

Skip silently only if NEITHER format's store files are present — not just because the JSON path specifically didn't match.

---

## Step 3 — Read before judging

Before auditing, read all injected/gathered context completely. Then ask:

1. What does this context confirm?
2. What does this context change?
3. What uncertainty has disappeared?
4. What uncertainty still remains?

The goal is not to collect more information — it's to determine whether enough now exists to implement confidently.

---

## Step 4 — Engineering review

### 1. Is the outcome obvious?
If two engineers independently completed this task, would they likely produce essentially the same implementation? If not, identify the ambiguity and recommend a clarification.

### 2. Can implementation begin immediately?
If not today, identify the missing information, the blocking decision, or the missing dependency.

### 3. Are assumptions hidden?
Look for assumptions that exist only in the author's head — architecture, API behavior, data format, ordering, deployment expectations. Validate what can be validated now; record the rest explicitly.

### 4. Is the task's own stated premise verified, not just assumed?
For infra/plumbing tasks especially — anything claiming "X is the authoritative file," "Y calls Z," "this is the production path," "this duplication is accidental drift" — spend one concrete verification step (git log/git show on the relevant file's history, grep for actual callers, live inspection of the running DB/service) before accepting the task body's framing at face value. Don't just implement what the task description says needs to happen. This caught three real bugs in one session (task:4b5bf21f: task description didn't mention 5 internal callers, only caught by the integration suite; task:46634a19: task named the wrong file for the actual production fix; task:9d3acbef: the task's entire premise — that a duplication was accidental — was wrong, and investigating why surfaced a separate real bug). See memory `verify-production-path-before-accepting-task-premise`.

### 5. Does historical context change the plan?
Review related tasks, related commits, code RAG, memories. Would you implement this differently after reading them? If yes, record it as a grooming note.

### 6. Is this task a duplicate or orphan?
Check it against its parent and siblings, not just unrelated related-tasks matches: does it restate the parent epic's own vision instead of a concrete piece of it? Does its `parent_id` actually match what its tags claim? Is its `project:` tag consistent with its siblings? Duplicate/orphan tasks are cheap to create by accident (parallel task creation, copy-paste) and expensive to leave live — they fragment ownership and waste future grooming passes. Flag explicitly, don't fold into a generic "conflicts" note.

### 7. Is the task appropriately sized?
Can this reasonably be completed in one focused implementation session? If not, recommend splitting into smaller subtasks.

### 8. What is most likely to stall implementation?
Predict the largest remaining risk — hidden coupling, unclear ownership, missing design decision, unknown API, migration uncertainty, missing tests. Record it.

This prediction is graded at introspection time (`/task-introspection` Step 3.0: materialized / avoided / wrong / missed), so state it concretely and falsifiably — "choosing the UPS injection mechanism will stall" can be graded; "there may be unknowns" cannot.

---

## Step 5 — Structural validation

Deterministic checks, run after the engineering review:

| Check | Pass condition | Flag |
|---|---|---|
| **Resolution format** | `Resolution:` exists and is a checklist (`- [ ]`) | "prose — convert to checklist" |
| **File paths named** | Each checklist item names a concrete file/module/subsystem | "file paths missing" |
| **Dependencies stated** | If this task needs another first, it's noted | "dependency on X not stated" |
| **Related task conflicts** | No related task contradicts this plan | "conflicts with task:<id> — <what>" |
| **Duplicate ownership** | No other task's checklist independently tracks the *same file edit* this task owns | "duplicates task:<id> on <file> — consolidate ownership" |
| **Prior art reused** | Related tasks/commits surface relevant existing patterns | "note prior art from task:<id>" |
| **Design decisions deferred** | No "TBD" where a concrete decision is needed to start | "decision needed: <what>" |
| **Concept invariant respected** | Plan does not violate stored invariants for touched files | "invariant risk: <module> — <invariant>" |
| **Checklist/status mismatch** | If every Resolution item is `[x]`, status is not left `open` | "all items checked but status is open — finish or explain what's still blocking" |

Duplicate ownership is distinct from a contradiction: two tasks can agree on *what* to do to the same file and still be a problem, because neither is the source of truth for when it's done. Consolidate to one canonical owner and have the others defer to it (link `relates_to`/`depends_on` via `tasks__link_tasks`), rather than leaving the same checkbox in two places.

---

## Step 6 — Update the task

Do **not** rewrite the body. Append a dated section:

```markdown
## Grooming Notes (YYYY-MM-DD)

### Clarifications
- ...

### Hidden Assumptions
- ...

### Risks
- ...

### Prior Art
- ...

### Suggested Improvements
- ...
```

```python
mcp__claude-hooks__tasks__update(id="<task_id>", body="<existing body>\n\n## Grooming Notes (...)\n...", mark_groomed=True)
```

`mark_groomed=True` sets the task's `groomed_at` timestamp — the structured signal that grooming ran (task:46634a19), distinct from the `## Grooming Notes` prose section. Always pass it on the update call that appends the notes, even when the notes are minimal, so `tasks__list`/`tasks__get` can surface "groomed" without a body substring search. Compare `groomed_at` against `updated_at` to detect staleness: if the body was edited after the last groom, treat the task as no longer confidently groomed.

If a duplicate/ownership consolidation was found, also call `tasks__link_tasks(from_id, to_id, relation_type="duplicates"|"depends_on"|"relates_to")` to record it structurally, not just in prose.

If a task looks like a duplicate/orphan warranting `abandoned` status rather than a note, don't decide unilaterally — surface it to the user (e.g. via a clarifying question) before changing status.

If no changes are required, leave the body untouched but still call `mcp__claude-hooks__tasks__update(id="<task_id>", mark_groomed=True)` to record that grooming ran, and note "ready as-is" in the report.

---

## Step 7 — Reset status

If activation changed the task's status, restore it to `open`:

```python
mcp__claude-hooks__tasks__update(id="<task_id>", status="open")
```

Grooming is preparation, not execution.

---

## Step 8 — Report

Per task:

```
✓ task:abc — ready  (title)
⚠ task:def — 2 gaps: file paths missing, decision needed: storage format  (title)
⚠ task:ghi — duplicates task:xyz on tools/db.py  (title)
```

Then a summary line:

```
N tasks groomed — M ready, K need updates.
```

If gaps were found: "Fix the flagged items and re-run `/task-grooming` before activating."

---

## Rules

- **Activation is mandatory** for anything below batch-size threshold (see Step 1). Related-task and diff-RAG context is only injected when a task is active — reading the body in isolation is not grooming.
- **Reset to open after grooming.** A groomed task is not a started task.
- **Don't rewrite the body — append.** Preserve the original task intent; add grooming notes as a dated section at the bottom.
- **Never guess the session_id.** Read from `## Turn state`, or use `hooks__session_id` if it isn't visible.
- **One task at a time below the batch threshold; disclose substitutions above it.** Don't silently skip activation for convenience — either do it, or say plainly that you didn't and why.

---

## Engineering philosophy

Every grooming pass should reduce uncertainty. Avoid editing merely for completeness. Instead ask:

* Does this make implementation easier?
* Does this eliminate a future planning pause?
* Does this reduce the chance of rework?
* Does this expose hidden assumptions?
* Does this make success more observable?

If the answer is no, the task probably does not need to change.

A successful grooming pass makes implementation boring. The engineer should be able to start coding immediately, with confidence, without needing another planning discussion.
