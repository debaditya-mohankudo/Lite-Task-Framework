---
name: discover-sysml-req
description: Check whether an existing requirement in this repo's SysML requirements model (models/requirements.sysml or model/requirements.sysml) already bears on a task or a piece of text under consideration. Content matching, not file matching — approximate by design. Use before creating a task that might duplicate or conflict with an existing guarantee, or while grooming a task whose motivation sounds like it touches a core invariant. Use when the user says /discover-sysml-req or asks "does a requirement already cover this?"
user-invocable: true
updated: 2026-08-03
doc: docs/methodology/03-grooming.md
repo: ~/workspace/task-framework/.claude/skills/discover-sysml-req/skill.md
deployed: ~/.claude/skills/discover-sysml-req/skill.md, ~/workspace/SeniorDevAgent/.claude/skills/discover-sysml-req/skill.md
---

Manual and callable, not wired into any other skill's automatic flow. That is
deliberate: this is new, and whether it earns a place inside `/task-grooming`
or `/task-create` should be decided from evidence of actual use, not assumed
up front. Invoke it yourself whenever the question is worth asking.

## What this is not

Not file-matching. `requirements.sysml`'s own `Source:` citations already give
an exact, deterministic way to ask "did this task's code touch what a
requirement depends on" — that question belongs to drift detection, not this
skill.

This is the other question: "does an existing requirement already bear on
what this is *about*," judged from what the requirement's doc text actually
says, independent of whether any file overlaps yet. That is approximate,
the same way `tasks__context`'s `related` section is approximate — a
candidate worth reading, never a claim that a requirement is satisfied,
violated, or must change.

## Args

One of:

- `task_id` — an existing task. Its title and motivation are the input.
- `text` — free text describing something under consideration. Usable before
  a task exists at all.

## Step 1 — Read the input

```python
tasks__context(task_id, verbosity="summary")   # if task_id given
```

Title and motivation are enough — this does not need the full bundle.

## Step 2 — Read the model

```bash
test -f models/requirements.sysml && echo models/requirements.sysml
test -f model/requirements.sysml && echo model/requirements.sysml
```

Repos disagree on whether the directory is singular or plural — check both and
use whichever resolves. If neither exists, say so in one line and stop. Most
repos have no SysML model; that is not a gap to flag.

Read the file directly. There is no query tool for `requirement def` content
— for 15 or so requirements in one file, reading is cheaper than building one,
and building one without a second use for it would be exactly the kind of
speculative structure this framework's own methodology argues against.

## Step 3 — Judge relevance

For each `requirement def`, ask: does the input sound like it asserts, relies
on, or could conflict with what this requirement's `doc` text already states?
Read the text, do not keyword-match it — a requirement's name rarely contains
the words that would make a mechanical match work.

An empty result is a valid, useful answer. Say "nothing in the existing model
looks related" rather than forcing a weak candidate onto the list — padding
here teaches the next reader to skim exactly the way an invented finding does
anywhere else in this framework.

## Step 4 — Output

```text
## Discovery: <task_id or a short label for the text> vs <models|model>/requirements.sysml

- <RequirementName> — <one line: why it looks related>
- <RequirementName> — <one line: why it looks related>
```

Or, if nothing surfaced:

```text
## Discovery: <label> vs <models|model>/requirements.sysml

Nothing in the existing model looks related.
```

Read-only. This skill does not link a task to a requirement, update the
model, or create anything — it surfaces candidates and stops. Whether to cite
one in a task's motivation, revisit the requirement, or author a new one is
the caller's call, not this skill's.
