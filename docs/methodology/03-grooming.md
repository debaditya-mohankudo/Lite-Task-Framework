# 3. Grooming

Grooming is not for making a task prettier. It is for **removing uncertainty
before implementation**, so that coding can begin without another planning
pause.

After a grooming pass, an engineer should know what to build, where, why that
way, what risks remain, and what success looks like.

## Read first

```python
tasks__context(task_id)      # task, decisions, prior grooming, graph, commits, related
```

Nothing is injected. Reading the task body alone is not grooming — the graph
and the neighbours are most of the signal.

**Treat existing `grooming` as a draft to revise, not a blank page.** Carry
forward what still holds, revise what changed, drop what is resolved, and add
what is new. A prior risk graded `avoided` or `materialized` is evidence about
what actually held up; discarding it throws away the most expensive information
in the task.

## Verify the premise

The highest-value step, and the easiest to skip.

For anything claiming "X is the authoritative file", "Y calls Z", "this is the
production path", or "this duplication is accidental" — **spend one concrete
verification step before accepting it.** Read the file. Grep for callers.
Check the git history. Inspect the running system.

This applies just as much to a premise you wrote yourself an hour ago as to one
you inherited. Self-authored premises are exactly as unexamined as inherited
ones, and feel more trustworthy, which makes them worse.

## Review

1. **Is the outcome obvious?** Would two engineers produce essentially the same
   implementation? If not, name the ambiguity.
2. **Can work start today?** If not, what decision or dependency is missing?
3. **What assumptions are hidden?** Architecture, data format, ordering,
   deployment. Validate what you can; record the rest.
4. **Does history change the plan?** Read the neighbours and the commits.
5. **Is it a duplicate or an orphan?** Check against parent and siblings, not
   just search results.
6. **Is it one session's work?** If not, split it.
7. **What will stall this?** Predict the largest remaining risk.

## Write findings

```python
tasks__update(task_id, grooming={
    "clarifications":       ["..."],
    "hidden_assumptions":   ["..."],
    "risks":                [{"text": "...", "graded": None}],
    "prior_art":            ["..."],
    "suggested_improvements": ["..."],
})
```

Findings go in `grooming`, not the motivation. Fixing the checklist, files, or
motivation itself is fine and encouraged — that is repair, not annotation.

`grooming` replaces wholesale: only the latest pass is kept. That is about
storage, not authorship — the object you pass should be an edited revision of
what was there.

## Risks must be falsifiable

Introspection grades every risk, so a risk that cannot be graded is noise.

> ✅ "Choosing the storage format will stall this — JSON column versus
> normalised tables is unresolved and the query patterns are unknown."
>
> ❌ "There may be unknowns."

The first can be graded `materialized`, `avoided`, or `wrong`. The second
cannot be graded at all, and so teaches nothing.

## Structural checks

| Check | Passes when |
|---|---|
| Resolution is a checklist | `resolution` has concrete items |
| File paths named | Items name a file or module |
| Dependencies recorded | Prerequisites exist as edges, not just prose |
| No duplicate ownership | No other task's checklist owns the same file edit |
| No blocking TBD | Nothing needed to start is unresolved |

Duplicate ownership is distinct from contradiction: two tasks can agree on what
to do and still be a problem, because neither is the source of truth for when
it is done. Consolidate to one owner and link the others.

## Grooming is not starting

A groomed task is not an active one. Do not leave it half-implemented because
grooming went well.
