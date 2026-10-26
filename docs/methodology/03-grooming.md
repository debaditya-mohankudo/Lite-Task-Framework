# 3. Grooming

Grooming is not for making a task prettier. It is for **removing uncertainty
before implementation**, so that coding can begin without another planning
pause.

After a grooming pass, an engineer should know what to build, where, why that
way, what risks remain, and what success looks like.

Everything below is in service of one question:

> **Do we understand this task, the existing behaviour, consequences,
> unknowns, risks, and complexity well enough to make an informed
> implementation decision?**

Grooming is done when the answer is yes — not when every field has been
filled in. A "no" is a legitimate outcome too, as long as what's blocking a
"yes" is named. Use judgement about what "well enough" requires for this
task; a one-line config change and a cross-service behaviour change do not
need the same amount of digging.

## Read first

```python
tasks__context(task_id)      # task, decisions, prior grooming, graph, commits,
                             # related, lessons
```

Nothing is injected. Reading the task body alone is not grooming — the graph
and the neighbours are most of the signal.

**The `lessons` section is loop memory, and it is here to be used.** Doc 05
tells introspection to record a constraint or a technique the moment it is
learned, and to read them back when grooming; this is that read. They arrive
matched approximately against the task's title and tags, so treat them the way
you treat `related` — as candidates, not as findings.

Each carries a derived `standing`. A lesson marked `confirmed` has held up on
later tasks; one marked `disputed` or `contradicted` is a claim to check, not a
fact to build on. If a lesson bears on this task, grade it rather than merely
reading it — that grading edge is the only thing that keeps a memory honest:

```python
task_memory__link(slug="...", task_id="<id>", relation="confirmed_by")
```

An empty `lessons` list means nothing matched. If the word `lessons` appears in
`truncated`, matches were dropped for space — pull them with
`task_memory__recall` rather than assuming there were none.

**Treat existing `grooming` as a draft to revise, not a blank page.** Carry
forward what still holds, revise what changed, drop what is resolved, and add
what is new. A prior risk graded `avoided` or `materialized` is evidence about
what actually held up. You no longer have to remember to re-paste it to keep
it — `tasks__update` carries a graded risk forward on your behalf when a
re-groom omits it, keyed by the risk's `id` rather than its text (task:f24be6e4).

## Investigate

There is no fixed checklist here — what counts as "understood well enough"
depends on the task, and a capable engineer chasing the guiding question will
naturally end up checking the things a checklist would have listed: whether
the plan is unambiguous, whether work can start today, what's hidden, what
history says, whether it's a duplicate or too large for one session, and what
would most likely stall it.

The one habit worth calling out because it's the easiest to skip: for
anything the plan rests on — "X is the authoritative file", "Y calls Z",
"this is the production path", "this duplication is accidental" — **spend one
concrete verification step before accepting it.** Read the file. Grep for
callers. Check the git history. Inspect the running system. This applies just
as much to a premise you wrote yourself an hour ago as to one you inherited —
self-authored premises are exactly as unexamined as inherited ones, and feel
more trustworthy, which makes them worse.

The second habit, skipped for the same reason: **name what the change could
break.** For every symbol or file the plan touches, ask what depends on it —
callers, subclasses, serialised formats, tests pinned to the current
behaviour — and whether the change alters what that dependent observes. A
"possibly" never goes in `hidden_assumptions`, which introspection never
grades, because a regression parked there has nowhere to surface. Where it
goes depends on which way you lean:

- **You expect the break** — a risk that names the bad outcome and how it
  happens.
- **You believe it is safe, and a read or run shows it now** — settle it and
  write a clarification.
- **You believe it is safe, but only the finished change can show it** — a
  verification item in `resolution`, which implementation ticks or fails in
  plain sight.

A safety belief filed as a risk ("changing X will not affect Y") is scored
against you even when it is right: introspection grades the bad event a risk
names, and a bad event that did not happen is `wrong`. In the 2026-10-26
baseline (task:25ab1953), 12 of 34 `wrong` risks were correct safety beliefs
filed this way. Size the blast-radius pass to the task, the same way
premise-checking is sized: a config tweak has almost no radius; a shared type
or a tool signature has a lot.

Much of what a change touches is not a prediction at all. Whether a test
asserts the behaviour being changed, whether a reader of a key still exists,
whether a file or database exists, whether a regeneration is byte-identical:
each can be read, so read it during grooming and write the answer down as a
clarification, naming what you read. A risk whose text already contains the
fact that settles it was never a risk. Four of the five wrong risks in
task:c0c5ff5f were "this will break test X". The rule that followed was
scoped to tests, and the baseline still had 13 wrong risks one read from
settled, only 2 of them about tests. Keep risks for what only the change can
reveal: what a caller will observe at runtime, performance, how existing data
migrates.

A guess about runtime behaviour — an import failing on this machine, a
permission the process lacks, an eval's starting score, a library warning on
stderr — is usually one short run from a fact. Run it. Nine of the 34 wrong
risks were guesses of this kind, and one live `ps` check while grooming
task:e0ce873e found a zombie-process case that reading the code had not. Run
the existing test suite once, too, and record what already fails; four of the
baseline's missed surprises were a suite already failing before the change,
with no record of it. Experiments stay inside a boundary: read-only commands,
scratch files, the existing test suite, dry-run or `--check` modes. Never
anything that sends, writes user data, deletes, or changes system state. An
experiment that would need one of those becomes an open question for the
user.

A deletion has a second radius, and it points the other way. Removing a
symbol can orphan what it read — a constant it compared against, a helper
only it called — and asking what depends on the deleted symbol never finds
that, because the question looks at its callers, not its inputs. So for each
symbol the plan deletes, check whether what it reads still has a reader once
it is gone. A mention in the ontology, concept store, or models describes code
rather than using it, and does not keep it alive. A framework that deletes
checks rather than keeping them should expect this leftover more than most.

When a claim is contested enough that a future reader would trust it without
re-checking, say plainly how well-supported it is: `fact`, `inference`,
`assumption`, or `unknown`. Most claims don't need the label — only the ones
where the distinction changes what happens next.

Consult the task graph, not just search hits, when judging whether something
is a duplicate or an orphan. Don't abandon a task unilaterally — surface it to
the user first, and consolidate duplicate ownership onto one task rather than
leaving two tasks that agree on the work but disagree on who's done with it.

## Write findings

```python
tasks__update(task_id, grooming={
    "clarifications":       ["..."],
    "hidden_assumptions":   ["..."],
    "open_questions":       [{"question": "...", "blocking": False}],
    "risks":                [{"text": "...", "graded": None}],  # "id" is assigned for you
    "prior_art":            ["..."],
    "suggested_improvements": ["..."],
})
```

`hidden_assumptions` is something believed and left unverified.
`open_questions` is something known to be unknown, with `blocking: true` when
work cannot start without an answer. Only add an entry where the distinction
changes what happens next.

Findings go in `grooming`, not the motivation. Fixing the checklist, files, or
motivation itself is fine and encouraged — that is repair, not annotation.

`grooming` merges per key: a key you pass replaces that key's list, and a key
you omit is left as it was (task:adc1353b). Only the latest pass is kept, but
that is about storage, not authorship — the object you pass should be an
edited revision of what was there. **To drop a key whose findings are all
resolved, pass it as `[]`** — omitting it keeps the stale entries live.
`risks` merges one level deeper, by `id` (task:f24be6e4): reword a risk by keeping its `id` and its grade
history follows; a new risk needs no `id` at all, one is assigned for you.
Leaving the `risks` key out entirely leaves the current risks untouched; pass
`"risks": []` to retract the ungraded ones (task:c0c5ff5f).

## Risks must be falsifiable

Introspection grades every risk, so a risk that cannot be graded is noise.

> ✅ "Choosing the storage format will stall this — JSON column versus
> normalised tables is unresolved and the query patterns are unknown."
>
> ❌ "There may be unknowns."

The first can be graded `materialized`, `avoided`, or `wrong`. The second
cannot be graded at all, and so teaches nothing.

Falsifiable is necessary, but it is not enough. A useful risk also names a bad
outcome you expect, not a belief that things will be fine. Nothing you could
read or run now would settle it. And it concerns this task: a prediction about
a later task, or about how someone will behave afterwards, cannot be seen by
this task's introspection, so it defaults to `wrong`. Such predictions belong
in `other` or in a follow-up task.

## Grooming is not starting

A groomed task is not an active one. Do not leave it half-implemented because
grooming went well.
