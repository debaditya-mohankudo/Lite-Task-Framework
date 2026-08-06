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

## Simplify first

Invoke the `simplify` skill on the pending diff before anything below. It
runs its own four-agent review (reuse, simplification, efficiency, altitude)
and applies what it finds directly — running it here, before the suite and
the commit, means whatever it fixes lands in the same commit instead of
needing a follow-up. Skip only if `simplify` was already run against this
exact diff earlier in the session with nothing left uncommitted since.

Run the four review agents in sequence, not in parallel — a user preference
to save budget, since the four passes together already cost more than
running them one at a time serially.

Simplify means simplify, not "simplify unless it breaks something": any
change it applies must not introduce a regression or break an existing test.
If a proposed simplification would do either, skip that specific change
rather than applying it — the rest of the diff still gets simplified. Verify
this against the suite run below, not by inspection alone.

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

Build it with `tasks__format_commit_message`, not by hand:

```python
tasks__format_commit_message(
    task_id="<id>",
    subject="Subject — the claim, in one line",
    body=(
        "Why this exists. What was true before that made it necessary, and what is true "
        "now. State failures directly rather than by reference to whatever they replaced; "
        "prose that explains a rule by naming what it replaced stops being legible to a "
        "reader who never saw the thing replaced.\n\n"
        "Design points that are load-bearing, especially anything that would look "
        "arbitrary to someone changing it later."
    ),
)
```

This returns the exact shape below — subject, blank, `task:<id>`, blank, body —
so the shape is produced once, correctly, instead of re-assembled from this
prose by hand every time. The tool only formats: it validates the task exists,
rejects an empty or multi-line subject, and strips a trailing period. It does
not touch the filesystem or git.

```
Subject — the claim, in one line

task:<id>

Why this exists. ...

Design points that are load-bearing...
```

Subject uses an em-dash, present tense, no trailing period. It states what
became true, not what was done.

The body is for **why**. The diff already says what changed, and restating it in
prose produces a message that goes stale the moment the code moves.

## Write the message to a file

Append the co-author trailer to the tool's returned `message`, then write the
result to a file in the scratchpad:

```
<tool's returned message>

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
```

```bash
git commit -F <path>
```

Heredocs and `-m` chains mangle multi-paragraph bodies, and a pasted block of
shell can end up as the message itself — that is what the file and `-F` avoid.

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

## Re-stamping a SysML model, if this commit touched a modelled path

`uv run pytest -q` (already run above) fails `tests/test_model_provenance.py`
if any `models/*.sysml` package's `modelledPaths` overlaps the files this
commit just touched — the stamp is now stale by definition, one commit late.

The fix is always a **separate follow-up commit**, never folded into the one
that just landed:

```bash
sha=$(git rev-parse HEAD)   # the code commit that just landed
```

This works with no prediction and no computation beyond that one command,
because of a structural fact worth stating plainly: the stamp-only commit
you're about to make never itself touches a modelled path (it only touches
the `.sysml` file, which is not in its own `modelledPaths` list), so
`git log`'s "last commit touching modelledPaths" keeps resolving to `sha`
even after the stamp commit lands on top of it. A commit's stamp cannot
reference its own hash — the tree the hash is computed over would have to
contain that same hash, which is circular and has no fixed point — but a
stamp commit was never trying to reference itself in the first place; it
only ever needs to reference the commit before it, which is already known.

Before bumping the stamp, re-read the model package's text against what
changed (per `tests/test_model_provenance.py`'s own instructions) — accurate
or not is a judgment call this step does not skip. Then:

```python
tasks__format_commit_message(
    task_id="<id>",
    subject="Re-stamp <package> after <short description of the code commit>",
    body="Why the stamp went stale, and confirmation that the package's text was re-read and still holds (or what was fixed if it didn't).",
)
```

Write, commit (`git commit -F <path>`), and link it with `tasks__add_commit`
exactly as above — it is its own commit against the same task, not an
amendment to the one before it.

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
