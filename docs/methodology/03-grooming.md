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
"possibly" is a falsifiable risk — "changing X will not affect Y" — and
belongs in `risks`, not `hidden_assumptions`, which introspection never
grades. A regression that no risk predicted has nowhere else to surface once
the work is done. Size this to the task, the same way premise-checking is
sized: a config tweak has almost no radius; a shared type or a tool signature
has a lot.

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

`grooming` replaces wholesale: only the latest pass is kept. That is about
storage, not authorship — the object you pass should be an edited revision of
what was there. The one exception is `risks`, which merges by `id` rather than
replacing (task:f24be6e4): reword a risk by keeping its `id` and its grade
history follows; a new risk needs no `id` at all, one is assigned for you.

## Risks must be falsifiable

Introspection grades every risk, so a risk that cannot be graded is noise.

> ✅ "Choosing the storage format will stall this — JSON column versus
> normalised tables is unresolved and the query patterns are unknown."
>
> ❌ "There may be unknowns."

The first can be graded `materialized`, `avoided`, or `wrong`. The second
cannot be graded at all, and so teaches nothing.

## Grooming is not starting

A groomed task is not an active one. Do not leave it half-implemented because
grooming went well.
