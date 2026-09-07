# Review: hot paths on the pull interface, and one scoped answer that hides its own omission

**Date:** 2026-09-07
**Scope:** `taskfw/{accuracy,context,scope,store,memory}.py`, `ontology/task-domain.json`,
`concept_store/concepts.json`, profiled against the live
store at `~/.taskfw/tasks.db` (1,799 tasks / 1,008 finished / 4,747 events / 138 memories, 18 MB).
**Question asked:** where is the framework paying more than it needs to, and where does an
answer it gives fail its own honesty standard?
**Baseline:** `pytest -q` → 610 passed, before any change.

---

## 1. Verdict

Two findings matter. One is a correctness-of-meaning defect in the tool whose entire job is
grading the loop; the other is a 20 ms subprocess on the framework's main entry point, for an
answer that cannot change within the process. Both are cases of a rule that already exists in
the codebase not being applied at a second site — not of a rule that needs inventing.

The other two findings are ordinary cleanups. One candidate that looked promising was checked
against the data and **rejected**; that is recorded in §4 so nobody spends the pass again.

---

## 2. Findings, ranked by leverage

### F1. `grooming_accuracy` does not say what a scoped call excluded — `taskfw/accuracy.py:271`

Live, same store, same scope, same moment:

```
loop_debt(limit=10, scope="git:github.com/…/lite-task-framework")
  → tasks_examined: 10, skipped_introspection: 0, ungraded_risks: 0,
    unscoped_not_counted: {skipped_introspection: 1, ungraded_risks: 3}

grooming_accuracy(limit=25, scope="git:github.com/…/lite-task-framework")
  → keys: [missed_surprises_groomed, missed_surprises_ungroomed, predictive_value,
           recurring_risks, risks, scope, self_report_disagreements, signals,
           skipped_introspection, tasks_examined, tasks_with_grooming,
           ungroomed_tasks_with_surprises]
    unscoped_not_counted: ABSENT
```

`store.list(scope=…)` is an exact match and therefore excludes every row written before
`Task.scope` existed. In this store that is **1,630 of 1,799 tasks (91%)**, and **876 of the
1,008 finished ones (87%)**. A scoped aggregate reads over ~11% of the corpus and reports
nothing about the remainder.

This is the framework's own rule breaking inside the framework's own module:

- CLAUDE.md: *"an omission must never be indistinguishable from an absence."*
- `_debt_counts` (`accuracy.py:162`) was extracted with the stated purpose that *"the scoped
  window and the unscoped remainder are counted by literally the same code — a 'what you are
  not being shown' figure computed by a second, slightly different rule would be worse than
  not showing it."* `grooming_accuracy` does not call it at all.
- `loop_debt`'s docstring argues the echo *"is not optional."* `grooming_accuracy`'s docstring
  states the exclusion in prose — where no caller can see it, and no caller reads docstrings.

The two functions answer the same question about the same store and disagree about whether
the caller is owed the remainder. That is one rule with two implementations, which is the
coupling this project exists to remove.

**Proposal.** Route `grooming_accuracy`'s scoped path through `_debt_counts` for the unscoped
remainder and emit the same `unscoped_not_counted` key, in the same shape, under the same
condition (present only when non-zero). No new rule, no new vocabulary — one existing helper
gaining its second honest caller. The test is that the prose sentence in the docstring can
then be deleted, because the result says it.

**Deliberately not proposed:** widening the scope filter to fold unscoped rows in. That would
invent a scope for 1,630 rows, which is the one thing `Task.scope` is not allowed to do, and
`store.list`'s docstring already refuses it for the right reason.

### F2. `local_root()` forks a subprocess on every context bundle — `taskfw/scope.py:187`

Measured, warm, same process:

```
local_root("git:github.com/…/lite-task-framework")   20.5 ms/call   (git rev-parse, every call)
derive()                                              0.05 ms/call   (cached at scope.py:71)
```

`tasks__context` calls `local_root` on every full bundle, to fill `files_root`. When the
task's scope is a `git:` scope matching the current workspace — the normal case while
actually working — that is a **20 ms `git rev-parse --show-toplevel` subprocess, roughly 40%
of the ~50 ms bundle**, on the pull interface that CLAUDE.md names as the sole replacement for
injected context. The agent pays it on every pull.

The answer cannot change within the process's lifetime. `derive()`, sixty lines above in the
same module, already caches for exactly that stated reason: *"Derivation is a subprocess and
both writers call it on every write, while a repository's origin does not change within a
process's lifetime."* The same sentence is true of the repository's toplevel, and the same
module holds both functions; only one of them acts on it. `reset_cache()` already exists for
the tests that build repos mid-process, so the escape hatch this needs is already built.

**Proposal.** Cache the `git rev-parse` result on the same terms `derive()` uses — keyed the
same way, cleared by the same `reset_cache()`. This is not a new mechanism; it is the existing
one reaching the second call site that has its precondition.

### F3. `_combination_score` re-tokenizes every candidate's whole body — `taskfw/store.py:174`

cProfile over 30 real bundles from the live store:

```
  30 calls   0.693 s cumulative   context.bundle
2520 calls   0.439 s tottime      store._combination_score      ← 65% of total
27,845 calls 0.165 s              re.Pattern.findall
751,928 calls                     str.lower
```

The scorer builds a ~500-word set from each of ~84 candidates in order to intersect it with
~6 query terms. The body word set is entirely thrown away; only its intersection with the
query is ever read.

Matching the query terms against the text directly — one compiled word-boundary alternation
per search, instead of materializing each candidate's vocabulary — measured **1.5–2.2× faster**
depending on query length, and produced **identical scores on 300 real tasks across three query
shapes (0 mismatches)**. Word-boundary semantics are preserved: "log" still does not score
against "dialog", which is the property the current docstring names.

Same 3:1 tag weighting, same disjoint tag/body sets, same results — strictly less work.

### F4. The FTS quoting rule has two implementations — `store.py:148`, `memory.py:301`

```python
quoted = ['"{}"'.format(t.replace('"', '""')) for t in terms]
```

Character-identical in both files, with the duplicated `" OR ".join(...)` and a parallel
LIKE-fallback shape alongside it. The rule it encodes is load-bearing — *a term is quoted so
punctuation reads as literal text, never as an FTS5 operator* — and it has two homes. Both
docstrings explain it; neither mentions the other. If either learns a fix, the other will not.

**Proposal.** One shared helper. Low urgency, listed because "every rule lives in exactly one
place" is the project's stated hard constraint and this is a plain violation of it.

### F5. The ontology reproduces F1's asymmetry, and one `Scope` claim is false in the data — `ontology/task-domain.json`

Checked after the code findings, and it turns out the vocabulary layer has the same shape of gap.

**F5a — `GroomingAccuracy` is the only term in its neighbourhood that does not mention scoping.**
Across 30 terms and 50 relations, `Scope` relates to `Task` and `Commit`, and `TaskContext`
*references* `Scope` with a note explaining precisely how its two approximate sections diverge on
it. `LoopDebtNudge`'s definition names the narrowing outright — *"how many of the last N finished
Tasks **in the project**"*. `GroomingAccuracy` says only *"gathered across many finished Tasks"*,
and **there is no `GroomingAccuracy → Scope` relation at all.**

So the ontology asserts the honest-narrowing rule on three neighbouring terms and drops it on the
fourth — the same one-site/not-its-twin shape as F1, one layer up. This is not incidental: `Scope`'s
own definition is where the rule is stated most sharply (*"a fallback must never compare equal to a
derived answer, or an omission becomes indistinguishable from an absence"*), and the term that most
needs it is the one term not joined to it.

**F5b — `Scope` claims the empty case is "never backfilled". It was.**

```
ontology Scope.definition:  "...carried by every Task written before the field existed,
                             never backfilled, and treated as compatible with every scope..."

concept scope-derivation:   "Derivation-once is a property of the CODE, and as of task:e62cefe8
                             no longer a property of the DATA. 105 pre-scope rows were stamped
                             by hand from this repo's own commit trailers."
```

Verified independently of both claims, straight from the store:

```sql
select count(*) from tasks
 where coalesce(scope,'')<>''
   and created_at < (select max(created_at) from tasks where coalesce(scope,'')='');
→ 103
```

103 scoped rows predate the last unscoped row — they carry a scope they could not have been
created with. The concept store records the hand-stamping and names the task that did it; the
ontology, describing the same field, still says it never happened. CLAUDE.md asks that the chain
resolve in both directions — *"a module's concept should be traceable up to the term it exists to
serve"* — and here the two ends contradict each other on the one property a reader would rely on.

`tests/test_ontology.py` passes (8 tests) and cannot catch either. It checks that each term's
evidence file exists and that the cited symbol appears in it as a substring; `taskfw/scope.py:derive`
and `taskfw/scope.py:local_root` are both still there, so the file is green while the sentence
around them is wrong. CLAUDE.md says as much in advance — *"Definitions, `note` prose, and the
accuracy of a relation's direction are not checked — treat it as a map that is caught drifting only
on a rename or a deleted file"* — which is exactly the failure mode observed here rather than a
surprise about it.

**Proposal.** F5a rides with F1: when the result learns to report its remainder, the term should say
it does, and a `GroomingAccuracy references Scope` relation should carry the note. F5b is a
one-sentence correction in a file F5a already opens — "never backfilled" becomes an accurate
statement that the field is never backfilled *by any code path*, with the 103 hand-stamped rows and
their provenance named, since they carry no marker distinguishing them from derived ones.

Note for F2: `taskfw/scope.py:local_root` is a cited evidence symbol for the `Scope` term. Memoising
it internally leaves the citation intact; renaming or inlining it would break `tests/test_ontology.py`.

---

## 3. Suggested order

1. **F1 + F2 + F5 together.** Independent of each other, small, and each closes a gap between a
   stated rule and a second site that does not honour it. F5a is F1's vocabulary half and should
   not land separately from it; F5b is a one-sentence correction in the file F5a already opens.
   One task — `task:de2b48b1`.
2. **F3.** Pure hot-path work on the same file F4 touches; can ride along.
3. **F4.** Cleanup.

---

## 4. Considered and rejected — verified, not assumed

**Backfilling `Task.scope` for the 1,630 unscoped rows from their own commits.** The obvious
repair for F1's 91%: `task_commits.repo` is normalised through `scope.for_repo`, so a task's
own recorded commits should carry a derived scope worth reading back. Checked against the
data:

```sql
select count(distinct t.id) from tasks t join task_commits c on c.task_id=t.id
 where coalesce(t.scope,'')='' and c.repo like 'git:%';
→ 0
```

Zero. Every commit belonging to an unscoped task predates the normalisation too, and holds one
of the old free-text spellings (`/Users/debaditya/workspace/task-framework`, `task-framework`,
`claude-hooks`, `''`, …) — the exact five-spellings fragmentation `scope.py` was written to
end. Recovering a scope from those would be inferring from a stale string, which
`store.add_commit`'s docstring already refuses by name: *"rewriting them would mean inferring
a scope from a stale string, which is reconstruction, not a record."*

So the route is correctly closed, and the corpus self-heals: every task created since the
column landed carries a real scope. F1 is the right fix precisely because it makes the
shortfall visible rather than papering over it.

**Making `tasks_fts` contentless to reclaim disk.** `tasks_fts` is declared
`fts5(id UNINDEXED, text)` with no `content=` option, so FTS5 keeps a full second copy of every
task's search text: `tasks_fts_content` is 4.2 MB of the 18 MB database (against 6.5 MB for
`tasks` itself). Tempting, and not recommended. A contentless FTS5 table cannot return column
values, and `search()` reads `f.id` back out of the index to join — so this would need either
an integer-rowid mapping table or a schema migration on the one table whose absence is already
handled as optional (`_try_enable_fts`). Added structure and a migration to reclaim 4 MB on a
local file is the wrong trade, and "structure only where it earns its keep" says so.

---

## 5. The one-line summary

Nothing here needs a new rule. F1, F2 and F5a are all a rule the codebase already states, applied
at one site and not at its twin — the honest-remainder rule that `_debt_counts` exists to
serve, the cache-the-subprocess rule that `derive()` already follows, and the scoped-narrowing
rule the ontology states on `Scope`, `TaskContext` and `LoopDebtNudge` but not on
`GroomingAccuracy`. F3 and F4 are the same shape one level down: work done twice that only
needed doing once. F5b is the one finding of a different kind — not a rule half-applied, but a
definition that stopped being true and had no test that could notice.
