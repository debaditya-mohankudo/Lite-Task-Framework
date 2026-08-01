---
name: commit
description: Commit changes in task-framework — the gate, the message shape, and what must be true before committing. Use when the user says /commit or asks to commit work in this repo.
user-invocable: true
updated: 2026-08-01
doc: docs/methodology/04-implementation.md
---

Commit procedure for this repo. Scoped here deliberately: these are
task-framework's conventions, and a global skill would impose them on repos that
never agreed to them.

## Before anything is staged

Run the suite. Not a subset, and not "the tests I think I touched" — the
coverage tests bind modules to concepts and the model, so an unrelated-looking
change can break them.

```bash
uv run pytest -q
```

**If it fails, say so and stop.** Do not commit, and do not describe the failure
as a pre-existing condition without checking whether it is. A summary that
overstates what landed is worse than no summary, because it will be believed.

## Every commit cites a task

```
task:<id>
```

on the second line, after a blank line. This is not decoration — the commit→task
edge is how `tasks__context` reassembles what happened later, and a commit with
no task is invisible to the loop that is supposed to learn from it.

Get the id from `tasks__active`. If there is no active task, ask which task this
belongs to. **Never invent an id, and never cite a task the work does not belong
to** — a false edge is worse than a missing one, because the missing one is
visible as missing.

## Message shape

```
Subject — the claim, in one line

task:<id>

Why this exists. What was true before that made it necessary, and what is true
now. State failures directly rather than by reference to whatever they replaced;
prose that explains a rule by naming what it replaced stops being legible to a
reader who never saw the thing replaced.

Design points that are load-bearing, especially anything that would look
arbitrary to someone changing it later.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
```

Subject uses an em-dash, present tense, no trailing period. It states what
became true, not what was done.

The body is for **why**. The diff already says what changed, and restating it in
prose produces a message that goes stale the moment the code moves.

## Write the message to a file

```bash
git commit -F <path>
```

Heredocs and `-m` chains mangle multi-paragraph bodies, and a pasted block of
shell can end up as the message itself. Write the message to a file in the
scratchpad, then pass it with `-F`.

## Link the commit to its task

There is no hook watching for this. After the commit succeeds, call it
yourself:

```python
sha = <output of git rev-parse HEAD>
tasks__add_commit(task_id="<id>", sha=sha, repo="<absolute repo path>")
```

**This step is easy to skip and nothing will stop you.** That is deliberate —
a pre-commit gate would make this a rule, and it isn't one — but it means the
link only exists if you make it. If you forget, or the call fails, the commit
is not lost: `python -m taskfw.backfill` re-derives every link from git
history regardless of whether this step ever ran. Prefer calling it anyway;
backfill is the recovery path, not a substitute for doing this.

## Splitting

Prefer one commit per idea. But `tests/test_concepts.py` binds each module to a
concept entry and the model to the concepts, so a split that separates a new
module from its concept produces a commit whose own tests fail.

**A green intermediate commit beats a tidy split.** If a boundary cannot be made
to pass, commit the coupled work together and say in the body why it was not
split.

## What never goes in

- Anything under a scratchpad path
- Generated artifacts, caches, `.venv`
- Deployed copies of files whose source lives in another repo — fix the source

## After

Report what landed: the subject, the files, and the test result as a number.
If something was left out of the commit, say what and why.
