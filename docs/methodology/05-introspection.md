# 5. Introspection

The purpose is not to remember the past. It is to make the **next** execution
better.

This is the step that closes the loop, and the easiest one to skip — the work
is done, the tests pass, and moving on feels like progress. Skipping it is what
makes grooming decorative, because ungraded predictions teach nothing.

## Grade the grooming

For every risk in `grooming.risks`, grade what actually happened:

- **materialized** — predicted, and it happened. Did the mitigation hold?
- **avoided** — predicted, and the prediction caused the change that dodged it.
- **wrong** — predicted, but irrelevant. Noise in that pass.
- **missed** — a surprise no prediction anticipated.

Be honest about the difference between *materialized* and *unresolved*. A risk
that said "decide X now" and was simply not decided has not materialized — it
is still open, and grading it as inevitable launders a skipped decision into a
recorded outcome. If grading surfaces a decision you skipped, **make the
decision now**; that is the loop working, not a failure of it.

Repeated `wrong` grades mean grooming is asking the wrong questions. Repeated
`missed` grades mean it is not asking enough of them. Both are signals to
change grooming itself, not just to note the miss.

## Ask

1. **Where did the uncertainty come from?** Could grooming have removed it?
2. **What decisions were never recorded?** Compare the plan to what was built.
   Log every gap — this is the highest-value part of the pass.
3. **What surprised us?** Should it become durable knowledge?
4. **What should already exist next time?** Prefer improving the system over
   documenting history.
5. **What became obsolete?** Flag it; do not delete it unilaterally.

## Record

```python
tasks__add_introspection(task_id, report={
    "date": "YYYY-MM-DD",
    "grooming_accuracy": {"predicted": N, "materialized": M, "avoided": K, "wrong": J},
    "missed_surprises": ["..."],
    "new_knowledge": ["..."],
    "highest_leverage": "The single most valuable improvement.",
    "overall_assessment": "One honest paragraph.",
})
```

Reports **append**, unlike grooming. Each is evidence about a distinct
execution, and the series is more useful than any one entry — a pattern across
three reports is worth more than the detail in any of them.

## Write decisions down properly

```python
tasks__add_decision(task_id, "...")
```

Anything discovered during the work that a future reader would need in order to
understand why the code looks the way it does. If the reasoning existed only in
the conversation, it did not survive.

## Keep it short

This is a two-minute activity, not a report. One line per finding. If nothing
surprised you and everything went smoothly, say exactly that in one line and
move on — padding it teaches the next reader to skim.

## What good looks like

A useful pass usually produces one of:

- A graded risk that changes how the next task is groomed.
- A decision that was made but never written down.
- A constraint discovered the hard way, now recorded so nobody rediscovers it.
- A named technique that worked, and is worth reaching for again.

If a pass produces none of those, it was probably not worth running — say so
rather than inventing findings to fill the shape.
