# Review: the spirit of the framework, and where the feedback loop is loose

**Date:** 2026-08-23
**Scope:** `CLAUDE.md`, `docs/methodology/`, `taskfw/{accuracy,context,dispatcher,memory,lifecycle,models}.py`,
`concept_store/concepts.json`, plus one live `tasks__grooming_accuracy(limit=25)` call.
**Question asked:** does the loop actually tighten, and where can it be tightened further?

---

## 1. Verdict

The stated spirit is honoured by the code more faithfully than is usual. The
things CLAUDE.md claims — derived-never-stored, one home per rule, omission
distinguishable from absence, advisory-not-blocking — are real and testable in
the source, not aspirational prose. `progress`, `task_phase`, memory `standing`,
and the accuracy tallies are all computed on read; `lifecycle.py` is genuinely
the only rule home; `context.py` reports what it dropped.

The weakness is not in any of those. It is that **the loop's fourth step is
well-specified and under-observed**, and the framework's own instrument says so.

### Live evidence

```
tasks__grooming_accuracy(limit=25)
tasks_examined: 25        tasks_with_grooming: 14
risks: total 22 — materialized 6, avoided 9, wrong 1, ungraded 6
missed_surprises: 5       predictive_value: 0.94
recurring_risks: []       skipped_introspection: [5f7d3d9a, 540cdb0d, e4dd0362, dfdcbac0]
```

Read honestly, that is:

- **29% of groomed, finished tasks (4/14) graded nothing.** CLAUDE.md names this
  as the drift signal that "actually happens." It is happening, at scale, in the
  repo that defines it.
- **27% of risks (6/22) are ungraded** even outside those four tasks.
- **1.6 risks per groomed task.** At that density the recurrence detector
  (`RECURRENCE = 2`, exact normalised text) can essentially never fire — and it
  hasn't: `recurring_risks: []`. The "see the series" edge exists in code and is
  inert in practice.
- **`predictive_value: 0.94` should not be read as good news.** Grooming chooses
  what to predict and introspection grades its own predictions, on a base of 16
  graded risks. A 94% hit rate on self-set predictions is closer to evidence that
  the predictions are safe than that grooming is sharp — especially next to 5
  missed surprises, i.e. roughly a quarter of all findings were things nothing
  predicted.

So: the per-task edge works when it is run. The *meta* edge — grading the
grader — is written, wired, and mostly not exercised. Everything below is aimed
at that.

---

## 2. Findings, ranked by leverage

### F1. A grade can be destroyed by the next grooming pass, and the defence is a doc sentence

`Task.grooming` is a plain `dict` replaced wholesale (`mcp_server.py:429`,
`updated.grooming = grooming`). A risk is `{"text": ..., "graded": ...}` with **no
identity**. Two consequences, both currently handled by asking humans to be
careful:

- `03-grooming.md` says treat prior grooming as a draft and carry it forward.
  A re-groom that doesn't manually re-paste graded risks silently erases the
  most expensive information in the task.
- `05-introspection.md` says *"Keep each risk's `text` byte-identical"*, because
  `accuracy._normalise` groups recurrence by text. A reworded risk stops matching
  its own history — silently.

This is the exact anti-pattern CLAUDE.md names: **a check (here, an instruction)
where the bad state could have been made unrepresentable.** Two rules that live
only in prose, enforced by nobody, protecting the loop's primary evidence.

**Proposal.** Give each risk a stable `id` (`r1`, `r2`, … or a short uuid),
assigned server-side on the grooming write when absent. Then:

- Grading is `{"id": "r3", "graded": "avoided"}` — a merge keyed by id, not a
  wholesale re-paste, so a grade cannot be dropped by omission.
- Recurrence groups by id-lineage where available and falls back to normalised
  text, so rewording a risk stops being a silent history reset.
- The "carry it forward" and "keep text byte-identical" sentences can be deleted
  from the methodology, which is the actual test that the fix was the right one.

Cost is one field and a merge path. It removes two policed rules and protects
the loop's only evidence. This is the highest-leverage change in the list.

### F2. Skipped introspection is detectable only by a tool nobody is obliged to call

`finish_nudge` fires once, inside the response to `tasks__finish`, and then the
debt is invisible until someone voluntarily runs `tasks__grooming_accuracy` —
which `05-introspection.md` explicitly says to run only "every few tasks."
Meanwhile four tasks sit closed and ungraded.

The framework's own doctrine covers this: a nudge is legitimate precisely
because it *"points back at context the agent already pulled."* Loop debt is
exactly that.

**Proposal.** Fold a `loop_health` block into `tasks__context` (or a nudge on
`tasks__set_active` / `tasks__create`) carrying only the two counts that are
already derived: number of finished tasks with ungraded risks, and number of
ungraded risks on *this* task. No new state, no push — the agent asked for
context, and this is a fact about the loop it is entering. Skipped introspection
is currently the one debt the system incurs at exactly the moment nobody is
looking, and is repaid at exactly the moment nobody asks.

### F3. Introspection writes lessons that grooming is never guaranteed to read

`task_memory` is where "a constraint discovered the hard way" and "a technique
worth reaching for again" go. `05-introspection.md` is blunt: *"Read them back
when grooming, or nothing recorded here was worth writing."*

But `context.py`'s bundle has six sections — task, decisions, grooming, graph,
commits, related — and **none of them is lessons**. A memory reaches an agent
only if it independently remembers `task_memory__recall` (confirmed as an
invariant in `loop-memory-derived-standing`). A write path with no guaranteed
read path is a diary, not a loop.

**Proposal.** Add a `lessons` section to the bundle: `recall(query=title + tags,
limit=3–5)`, placed after `grooming`, inserted into `TRIM_ORDER` just after
`related` (approximate, so trimmed early), and named in `truncated` when dropped.
This is still a pull — the agent called `tasks__context` — and it is the single
change that makes recorded lessons load-bearing rather than optional.

Second-order benefit: memories then actually get recalled, which makes their
`confirmed_by` / `contradicted_by` grading possible, which is the *other*
ungraded prediction edge in the system.

### F4. `missed_surprises` is the highest-signal artifact and it is a dead end

CLAUDE.md: *"A surprise is the cheapest knowledge available, and it evaporates
within the hour."* Five have been recorded. They are free strings in an
introspection report. Nothing groups them, nothing detects their recurrence
(`recurring_risks` covers predicted risks only), and nothing pushes them toward
becoming either a memory or the next pass's predicted risk.

A surprise that recurs is the strongest possible instruction to change what
grooming asks — and it is precisely the thing `accuracy.py` cannot see.

**Proposal.** Two small, read-side-only additions to `grooming_accuracy`:
`recurring_surprises` (same `_normalise` + `RECURRENCE` treatment already applied
to risks) and a `surprise_share` count. Optionally extend `introspection_nudge`,
which already spots unpromoted lessons, to also flag a report carrying a
surprise that has appeared in a prior task's report — that is a pattern, and
right now it is invisible by construction.

### F5. The meta-loop has no time axis, so it cannot show improvement

`grooming_accuracy` returns one flat aggregate over the last 25 finished tasks.
The methodology's whole prescription is behavioural — *"repeated `wrong` means
change what grooming asks"* — but nothing shows whether a change to grooming
helped. The instrument that grades the grader is itself ungraded.

**Proposal.** Report the same tallies split into two recency buckets (e.g. most
recent 10 finished vs. the 10 before), plus the delta in `predictive_value` and
`surprise_share`. Pure read-side derivation, no schema change, no new state. It
converts "grooming is at 0.94" into "grooming got sharper / duller since the last
change," which is the only form of that number that can drive a decision.

While there: consider suppressing or explicitly caveating `predictive_value`
below a sample floor. `MIN_SAMPLE` already gates the *signals*; the headline
ratio itself is reported at any n, and a flattering number with n=16 on
self-set predictions is more likely to be believed than interrogated.

### F6. Collected-but-unread usage data

`memory.hit_count` / `last_hit` are bumped on every recall and, per the concept
store's own invariant, *"nothing currently reads them"* (task:582dba10, open).
That is a small, honest instance of collecting a signal nobody grades. Either
wire them into a staleness signal (a memory never recalled since N tasks is a
candidate for supersede review) or drop the columns. Low urgency; listed for
completeness because the framework's standard is that instrumentation should
close a loop or not exist.

---

## 3. What is fine and should not be "improved"

Per the framework's own rule against inventing findings to fill a shape:

- The nudge architecture is right. Advisory-only, stateless where cheap,
  throttled where hot, no dispatch registry, every nudge composed at its own
  decorator line. The rejected-registry reasoning in `advisory-nudge-dispatcher`
  is sound and should stay rejected.
- `lifecycle.py` is genuinely one home per rule, and the three rules it
  *deleted* rather than relocated are the best evidence in the repo that the
  stated technique is actually practised.
- `context.py`'s trim discipline — whole sections, never partial, always named
  in `truncated` — is the correct reading of "an omission must never be
  indistinguishable from an absence." (One known asymmetry, already recorded in
  the concept store: `MAX_DECISIONS` / `MAX_COMMITS` / `MAX_RELATED` cut
  silently, while `MAX_EDGES` reports. Worth closing eventually, but it is a
  known, documented gap, not a discovery.)
- Two types, four statuses, one hierarchy rule. Resist every future pull toward
  a third type. Note that F1 adds a *field*, not a category — it makes a bad
  state unrepresentable rather than asking anyone to classify anything.

---

## 4. Suggested order

1. **F3 — lessons in the context bundle.** Smallest change, largest immediate
   effect: it makes an entire existing subsystem load-bearing.
2. **F1 — stable risk ids.** Protects the evidence everything else is computed
   from, and deletes two prose-only rules.
3. **F2 — loop debt surfaced at task start.** Directly targets the 4 skipped
   introspections, which the framework itself calls the failure that actually
   happens.
4. **F4 / F5 — surprise recurrence and a recency split.** Read-side only; makes
   the meta-loop capable of showing whether it is working.
5. **F6 — use or remove `hit_count` / `last_hit`.**

Each of 1–3 is a candidate task in its own right; 4 and 5 are one task together.

---

## 5. The one-line summary

The loop's per-task edge is well built. Its weak points are all the same shape:
**evidence is written where nothing is obliged to read it** — grades into a blob
that the next pass can overwrite, lessons into a store the bundle never surfaces,
surprises into an array nothing aggregates. Tightening the loop here means fewer
new mechanisms and more guaranteed reads.
