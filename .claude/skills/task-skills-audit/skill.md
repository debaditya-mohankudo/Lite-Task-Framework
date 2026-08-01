---
name: task-skills-audit
description: Audit the task-* skills for drift — against each other, against the methodology docs, and against the live MCP tool surface. Use when the user runs /task-skills-audit or asks whether the task skills are still consistent.
user-invocable: true
updated: 2026-08-01
doc: docs/methodology/README.md
---

## Purpose

The lifecycle is a chain of five skills, each edited independently:

```
task-create → task-framework → task-grooming → task-implementation → task-introspection
```

They reference each other constantly, and they sit on top of two things that move underneath them: [docs/methodology/](../../../docs/methodology/), which owns the reasoning, and `taskfw/mcp_server.py`, which owns the tool surface. Nothing keeps prose in sync with either.

This is the same class of risk the repo's other parity checks address, and it has already bitten once: these skills were carried over from a system with a different tool surface and referenced roughly a dozen tools that do not exist here.

**Read-only by default.** Flag drift; let the user decide.

## What is checked mechanically

`tests/test_skills.py` already enforces the parts that can be enforced:

- every `tasks__*` and `concept__*` name a skill mentions exists on the server
- no `mcp__<server>__` prefixes, which would tie the skills to one host's wiring
- no `session_id`, which the framework has no concept of
- every relative markdown link resolves
- frontmatter carries `name`, `description`, `user-invocable`, `updated`, `doc`, and `doc` points at a real methodology file

Run it first — anything it catches needs no judgment:

```bash
.venv/bin/python -m pytest tests/test_skills.py -q
```

Everything below is what a test cannot check.

## 1. Skill ↔ methodology drift

Each skill's frontmatter names its `doc`. The division is deliberate: **the doc owns the reasoning, the skill owns the invocation.** Read both and check:

- Does the skill contradict its doc anywhere? The doc wins — it is the portable asset; the skill is one host's delivery mechanism.
- Has the skill grown a substantial rationale that belongs in the doc? Duplicated reasoning is a new drift surface.
- Has the doc gained a step the skill never mentions?

## 2. Cross-reference accuracy

Build a list of every claim one skill makes about another's mechanism, then verify each against that skill's current text. The kind of claim to check:

- task-framework's step ordering against what task-grooming actually does
- task-grooming's "risks must be falsifiable" against how task-introspection grades them
- task-introspection's byte-identical-text rule against what `tasks__grooming_accuracy` actually groups by
- task-create's field descriptions against `tasks__create`'s real signature

Do not guess which side is stale. Describe the mismatch and let the user decide.

## 3. Tool surface completeness

The question that finds real gaps: **does a tool exist that a skill should mention and does not?**

```python
tasks__list()   # any tool name in mcp_server.py absent from every skill
```

Compare the registered tools against what the skills reference. A tool nobody is told to call is a tool nobody calls — this is how `tasks__grooming_accuracy` would have gone unused after being built. Absence from the skills is a finding, not an oversight to fix silently.

Also check the reverse of the mechanical test: a concept in `concept_store/concepts.json` describing a workflow the skills never invoke.

## 4. Vestigial machinery

These skills were ported from a system with more moving parts. Watch for anything that assumes machinery this framework deliberately does not have:

- a session id, or any per-session state
- an activation step that fetches context (activation sets a pointer; `tasks__context` fetches)
- a body template, required sections, or a scaffolding tool
- issue types beyond `epic` and `task`
- a `mark_groomed` flag or `groomed_at`/`introspected_at` column
- an indexing or re-indexing step
- memory, code-search, or diff-search tools from another server

Each of those is a thing the framework removed on purpose. A skill that still assumes one is describing a system that does not exist.

## Report

```
## task-skills-audit: N skills checked

Mechanical (tests/test_skills.py)   ✓ pass / N failures
Skill ↔ doc drift                   - <skill>: <claim> contradicts <doc>
Cross-reference mismatches          - <A> claims <X> about <B>; <B> says <Y>
Unreferenced tools                  - <tool> exists but no skill mentions it
Vestigial machinery                 - <skill>: assumes <thing that does not exist>

N found — fix now, or file as a task?
```

If nothing is found, say so in one line.

## Rules

- **Run the test first.** Do not spend judgment on what a parser already checks.
- **Read-only by default.** Never edit without the user choosing to fix.
- **The doc wins over the skill** where they disagree on reasoning. The skill wins on invocation detail.
- **Do not guess which side of a cross-reference is correct.** Describe both.
- **This is judgment, not scoring.** Consistency between prose descriptions is not mechanically measurable the way a schema parity test is — treat it the way grooming treats an engineering review.
