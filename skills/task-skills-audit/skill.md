---
name: task-skills-audit
description: Audit the task-* skills (task-create, task-framework, task-grooming, task-implementation, task-introspection) for feedback-loop consistency — cross-references, frontmatter, and live/repo drift. Use when the user runs /task-skills-audit or asks to check whether the task skills are still consistent with each other.
user-invocable: true
updated: 2026-07-26
repo: ~/workspace/claude-hooks/skills/task-skills-audit/skill.md
deployed: ~/.claude/skills/task-skills-audit/skill.md
---

## Purpose

The task lifecycle is a chain of five skills, each maintained and edited independently:

```
task-create → task-framework → task-grooming → task-implementation → task-introspection
```

They reference each other constantly — task-framework's Step 0b says "run /task-grooming", task-implementation's frontmatter says "after /task-grooming and before /task-introspection", task-grooming's Step 6 sets `mark_groomed=True` which task-introspection's Step 3.0 grades against. Nothing mechanically keeps these cross-references accurate as each skill evolves on its own. This is the same class of risk as `src/db/schema.py`/`src/tools/tasks.py` drifting apart (task:9d3acbef) — two things that must stay in sync, with nothing enforcing it except someone noticing.

This skill is **read-only auditing, not auto-fixing** — same standard as `/memory-audit`: flag drift, let the user decide.

---

## What to check

### 1. Frontmatter consistency
All five skills should declare the same shape of frontmatter — `name`, `description`, `user-invocable`, `updated`, and (for skills with a repo counterpart) `repo:`/`deployed:` pointing at matching paths. Flag any skill missing fields the others have, or using an outdated convention (e.g. an HTML source-of-truth comment instead of `repo:`/`deployed:` frontmatter — this exact case existed in `task-framework` until caught 2026-07-26).

### 2. Live vs repo copy drift
For every skill with `repo:`/`deployed:` frontmatter, diff the two paths:
```bash
diff <repo path> <deployed path>
```
Any non-empty diff is drift — one was edited without syncing the other. This has happened twice already in one session (task-grooming, task-framework) from editing the live copy directly and forgetting the repo copy, or vice versa.

### 3. Cross-reference accuracy
Read all five skill files fully, then build a list of every claim one skill makes about another's mechanism or behavior — a step name, a field it sets, a grading rule it feeds. For each claim, verify it against the referenced skill's *current* text. Examples of the kind of claim to check:
- Does task-framework's Step 0b actually match what task-grooming's Steps 1–8 currently do?
- Does task-grooming's `mark_groomed=True` call (Step 6) match what task-introspection's Step 3.0 says it grades?
- Does task-introspection's Step 6 reference a specific `/task-grooming` step number that still exists at that number after edits (numbering shifts — e.g. task-grooming's Step 4 items were renumbered 2026-07-26 when a new item was inserted; anything citing the old number is now stale)?
- Does task-create's "quick reference" match what `tasks__create`'s actual required body sections currently are (Type/Task/Resolution/Motivation/Files, or Type/Task/Finding/Context for research)?

Flag any claim that no longer matches. Don't guess what the fix should be — describe the mismatch and let the user decide which side is stale.

### 4. Lifecycle completeness
Ask: is there a mechanism that exists in the codebase (an MCP tool param, a DB column, a concept-store convention) that one of these skills should reference but doesn't yet? This is how `task-framework`'s missing concept-store check (task:85302a63) and `task-grooming`'s missing `mark_groomed` call (task:46634a19) were found — not by diffing text, but by asking "does this skill's step actually use everything relevant that exists now."

---

## Steps

### 1. Read all five skills
Read the *live* copies (`~/.claude/skills/task-*/skill.md`) in full — these are what's actually active for the user.

### 2. Diff live vs repo for each
```bash
for f in task-create task-framework task-grooming task-implementation task-introspection; do
  diff ~/.claude/skills/$f/skill.md ~/workspace/claude-hooks-dev/skills/$f/skill.md
done
```
Report any non-empty diffs as drift (Check 1 above). Use the dev-worktree repo copy for this — same convention this repo's other skill edits follow (edit dev, sync live, commit dev, deploy).

### 3. Frontmatter pass
Extract each skill's frontmatter block, compare `name`/`description`/`user-invocable`/`updated`/`repo`/`deployed` fields across all five for consistency (Check 1).

### 4. Cross-reference pass
Read for cross-references (Check 3) — this is qualitative judgment, not a mechanical parse. Note each claim, the skill it's checked against, and whether it still holds.

### 5. Lifecycle completeness pass
Ask Check 4's question directly — has anything shipped in `src/tools/tasks.py`, the concept store, or elsewhere that one of these skills should now mention but doesn't?

### 6. Report

```
## task-skills-audit: N skills checked

### Frontmatter
- <skill>: missing <field> / uses outdated convention

### Live/repo drift
- <skill>: N lines differ (not synced)

### Cross-reference mismatches
- <skill A> claims <X> about <skill B>, but <skill B> currently says <Y>

### Lifecycle gaps
- <mechanism> exists in <file> but isn't referenced by any task-* skill

N found — recommend fixing now, or file as a task?
```

If nothing found: say so in one line, don't pad.

### 7. Fix only on confirmation
For each item the user wants fixed: edit the repo copy, sync to the live copy (`cp <repo> <deployed>`), and if the user is tracking it, create or update a task the same way `task:46634a19`/`task:85302a63` did — don't silently apply fixes without being asked, matching `/memory-audit`'s "never auto-delete/auto-fix" rule.

---

## Rules

- **Read-only by default.** Report drift/mismatches; never edit without the user choosing to fix.
- **Always diff live vs repo, not just read one.** The live copy is what's actually active; the repo copy is what ships. Either can be stale relative to the other.
- **Don't guess which side of a mismatch is "correct."** Describe both and let the user decide — a cross-reference mismatch might mean the referencing skill is stale, or the referenced skill regressed.
- **This is a judgment task, not a scorer.** Consistency between prose descriptions of behavior is not mechanically measurable the way a schema-parity test is (see task:9d3acbef) — treat this the way `/task-grooming` treats an engineering review, not the way a fuzzy-logic collector treats a diff.
