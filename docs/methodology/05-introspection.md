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
- **missed** — a surprise no prediction anticipated. This has no risk in
  `grooming.risks` to grade against — it is not one of the four grades above,
  it goes in the introspection report's `missed_surprises` field instead.

Be honest about the difference between *materialized* and *unresolved*. A risk
that said "decide X now" and was simply not decided has not materialized — it
is still open, and grading it as inevitable launders a skipped decision into a
recorded outcome. If grading surfaces a decision you skipped, **make the
decision now**; that is the loop working, not a failure of it.

Write the grades back by re-passing the whole grooming object with `graded`
filled in, keeping each risk's `id` (from the grooming bundle) so the grade
attaches to the right risk:

```python
tasks__update(task_id, grooming={
    **existing_grooming,
    "risks": [
        {"id": "<id from the bundle>", "text": "<unchanged or reworded>", "graded": "avoided"},
        {"id": "<id from the bundle>", "text": "<unchanged or reworded>", "graded": "wrong"},
    ],
})
```

**`grooming` replaces wholesale**, so pass the other keys back unchanged or
they are lost. `risks` is the one exception (task:f24be6e4): it merges by
`id`, so a risk you omit here is carried forward automatically if it was
already graded, and rewording a risk's `text` no longer resets its history —
the `id` is what recurrence and grading key on now, not the wording.

Repeated `wrong` grades mean grooming is asking the wrong questions. Repeated
`missed` grades mean it is not asking enough of them. Both are signals to
change grooming itself, not just to note the miss.

## See the series, not just this task

```python
tasks__grooming_accuracy(limit=25)
```

"Repeated" is a claim about several tasks, and one task cannot show it. This is
the only tool that reads across tasks: it aggregates the grades on recent
finished work and returns the tallies, the risks that keep recurring, and the
finished tasks whose risks were never graded at all.

Run it every few tasks rather than every time — a pattern needs a series before
it is a pattern. Act on what it returns:

- **Low predictive value** — grooming is generating noise. Ask fewer, sharper
  questions.
- **A recurring risk** — the same thing keeps threatening work. Fix the cause
  or add it to the structural checks, rather than predicting it again.
- **Skipped introspections** — the loop is not running. Nothing else here
  matters until that is true.

Tallies are recomputed from the individual grades rather than read back from
the `grooming_accuracy` counts in the reports, so a report that overstates its
own accuracy is reported as a disagreement instead of being believed.

## Assess behaviour, not incidental detail

Judge the task by what a user or caller can observe: the promised outcome,
boundaries, failure behaviour, and invariants. Ask whether those promises held
in reality, including paths that fail quietly. An internal implementation
choice is worth recording only when it affected behaviour, safety, clarity, or
an established convention.

A useful retrospective preserves the behaviour that matters while leaving
future work free to improve the internals. Capture durable contracts and causal
lessons, not a transcript of implementation details.

## Ask

1. **Did the intended behaviour hold?** Compare the promised outcomes,
   boundaries, failure behaviour, and invariants to what callers actually
   experienced.
2. **Where did the uncertainty come from?** Could grooming have removed it?
3. **What decisions were never recorded?** Compare the plan to what was built.
   Log every gap — this is the highest-value part of the pass.
4. **What surprised us?** Should it become durable knowledge?
5. **What should already exist next time?** Prefer improving the system over
   documenting history.
6. **What became obsolete?** Flag it; do not delete it unilaterally.

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

## Record the lessons that belong to no task

Three of the four things a good pass produces already have homes: a graded risk
goes in the task's grooming, a decision in `tasks__add_decision`, an
architectural fact in the concept store. The other two — **a constraint
discovered the hard way** and **a technique worth reaching for again** — belong
to no single task and no single module.

```python
task_memory__record(slug="degrade-fts-to-like", task_id="<id>", kind="constraint",
                    text="FTS5 is a compile-time option, so search must degrade rather than fail.")
```

`kind` is `constraint`, `technique`, or `pitfall`. The task id is required: a
lesson with no evidence is an opinion, and the citation is what lets a later
task check it.

Read them back when grooming, or nothing recorded here was worth writing:

```python
task_memory__recall(query="<what you are about to do>")
```

### A memory is a prediction, and later tasks grade it

Recording one claims a lesson generalises. That claim is graded exactly as a
groomed risk is:

```python
task_memory__link(slug="...", task_id="<id>", relation="confirmed_by")
task_memory__link(slug="...", task_id="<id>", relation="contradicted_by")
```

Standing is computed from those links — `unverified`, `confirmed`, `disputed`,
`contradicted` — never stored, so it cannot drift from its own evidence. A
disputed memory comes back marked disputed rather than as settled fact; the
store cannot know which side is right, and you can.

### Obsolete, not deleted

```python
task_memory__supersede(slug="<old>", by="<new>")
```

The row survives and names its replacement, and drops out of recall. That is
the same standard as flagging stale knowledge rather than removing it
unilaterally. `task_memory__forget` exists for a lesson that was simply
**wrong** — a different thing from one that stopped being true.

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
