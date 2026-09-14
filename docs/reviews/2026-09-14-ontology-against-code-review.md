# Review: the ontology read against the code it names

**Date:** 2026-09-14
**Scope:** `ontology/task-domain.json`, `ontology/README.md`, `tests/test_ontology.py`, read against
`taskfw/{task,risk,lifecycle,store,memory,accuracy,context,mcp_server}.py`,
`taskfw/dispatcher/*`, `taskfw/db/schema.py`.
**Question asked:** where can the ontology be simplified or improved, and what does reading it next to
the code show about the code?
**Method:** read by hand, plus scripted checks over the JSON (evidence parsing, relations implied
by `is-a`, form consistency, uncited modules). Findings marked *verified* were reproduced or
grepped. The rest come from reading the code and are not proven.

---

## 1. Verdict

The ontology is mostly accurate. Its main weakness is size: 12 of 34 definitions break the
README's own closed-vocabulary rule, and several say the same thing as a relation note.
Reading it next to the code turned up one real bug (A1) and three naming collisions (A2–A4)
where the code says in two ways what the ontology can only say in one. The test checks less
than it seems to: two of its evidence fields are never checked, and some of their values are
already broken (D1).

---

## 2. Code findings

### A1. Re-grooming a legacy id-less graded risk erases its grade — `taskfw/mcp_server.py:474` (*verified*)

In `_merge_grooming_risks`, an incoming risk with no id that text-matches a current risk with
no id builds its new entry from the incoming dict only, so the current grade is dropped. The
carry-forward loop then skips that risk because it was marked consumed.

```
_merge_grooming_risks([{"text": "Legacy risk X", "graded": "materialized"}],
                      [{"text": "legacy risk x."}])
  → [{'text': 'legacy risk x.', 'id': '85b44969'}]          # grade gone

_merge_grooming_risks([{"id": "a1", "text": "Risk Y", "graded": "avoided"}],
                      [{"id": "a1", "text": "Risk Y reworded"}])
  → [{'id': 'a1', 'text': 'Risk Y reworded', 'graded': 'avoided'}]   # id path keeps it
```

This contradicts the ontology's `Risk` / `GroomingGrade` claim ("never lost") and the id path
of the same function. Only risks written before task:f24be6e4 can hit it.

### A2. Nudge response keys don't match the nudge names (*verified*)

`finish_nudge` appears in responses as `introspection_nudge` (`mcp_server.py:188`), and
`introspection_nudge` appears as `memory_nudge` (`:255`). What the ontology calls
`IntrospectionNudge` reaches callers as `memory_nudge`, while a different nudge uses the name
`introspection_nudge`. Fix: have `apply_nudge` take the nudge function and use its `__name__`
as the key, so a mismatch can't happen. This changes response keys, which are pinned in
`tests/test_mcp_tools.py:345-356, 705-732` and mentioned in
`models/task_framework_system.sysml:105`. No skill reads them.

### A3. `Decision` means two things

`lifecycle.Decision` is a rule-check result. The domain `Decision` is an Event whose kind is
`decision`. `_finish_task` sets `decision = lifecycle.check_transition(...)` (`:620`) a few lines
from `add_event(kind="decision")`. Renaming the lifecycle class (e.g. `Ruling`) removes the
clash.

### A4. `scope` means two things

`mcp_server._scope()` returns the working directory, so `tasks__active` / `tasks__set_active`
return `"scope": "/path"`, while `tasks__grooming_accuracy` returns `"scope": "git:..."`: the same
key with different kinds of value. The ontology spends a relation note explaining "despite that
function's name", and its README calls a definition that can't be written cleanly a design
finding. Renaming to `_workspace()` / `workspace` removes the need for the note.

### A5. `check_event_kind` checks that can never fail

It is only called with hard-coded strings: `"status"` (`:624`) and `"decision"` (`:844`). No caller
ever writes the `note` kind; it is only the store's default. The project's rule is to remove
a failure mode rather than police it, so either drop these calls or route a caller-supplied
kind through the check.

---

## 3. Ontology simplifications

- **B1. Drop `TaskStore`.** It is storage plumbing and the only user of the `persists` predicate.
  `MemoryStore` and `ConceptStore` have no terms. Predicates go from 6 to 5.
- **B2. Drop `SkillInvocation`.** It is filed under Loop Memory with form `record`, but its own
  definition says it is not a record. It is a log line.
- **B3. Fold `IntrospectionNudge` and `LoopDebtNudge` into `Nudge`.** Only 2 of 7 nudges have
  terms, and the notes spend paragraphs explaining why. CLAUDE.md's drift list names this: "a
  new type ... to express something a tag would have carried." `concept_store/concepts.json:85`
  would need updating.
- **B4. Remove relations already implied by `is-a`** (*verified*): `Decision relates Task`
  follows from `Event`, and `IntrospectionNudge references Introspection` repeats
  `Nudge references Introspection`.
- **B5. Take implementation detail out of definitions.** 12 of 34 name `GROOMING_TRIM_ORDER`,
  `taskfw/accuracy.py:loop_debt`, `task:e62cefe8`, `tasks__set_active`, and similar. `Scope` runs to
  1,448 characters, including a data-provenance story that `concept:scope-derivation` already
  holds.
- **B6. Stop saying things twice.** Commit's and Event's definitions nearly repeat their relation
  notes, and the `TaskEdge relates Task` note holds Task's parent rules.
- **B7. (Optional) Demote `GroomingGradePredictiveValue` to a note on `GroomingAccuracy`.**
  `self_report_disagreements`, the same kind of field, was deliberately left as a relation.

## 4. Missing vocabulary

- **C1. `TaskPhase`** (groomed / implemented / introspected): a derived value that the nudges and
  `tasks__phase` share (`taskfw/dispatcher/phase.py`, which no term cites).
- **C2. `Progress`.** Several definitions say "same reason as progress", but it is not a term,
  which breaks the closed vocabulary.
- **C3. Supersession.** A MemoryRecord superseding another (`superseded_by`) drives the
  `superseded` standing and hides the memory from recall, but no relation records it. The
  difference between forget (wrong) and supersede (outdated) is domain language too.
- **C4. Pass vs. record.** `Grooming` and `Introspection` are each `part-of Methodology` (the
  pass) and `part-of Task` (the stored section), while `Implementation` is a `process`. Document
  this in the README rather than adding terms.
- **C5. `Decision`'s form.** It is `attribute` but `is-a Event`, which is a `record` (*verified*).
  The README uses it as its example of an attribute.

## 5. Test gaps

- **D1. `lifecycle_evidence` and relation `evidence` are never checked, and some are already
  broken** (*verified*). Task's `"skills: task-create, …"` reads as a file called `skills`.
  The lifecycle evidence for `ActiveTaskPointer` and `GroomingGrade` can't be parsed.
  TaskStatus's `models/task_lifecycle.sysml` is read as a symbol inside `taskfw/lifecycle.py`.
  The test should cover all three fields and allow citing a whole file with no symbol.
- **D2. Dotted symbols only check the first part.** `TaskStore.set_active` passes as long as
  `TaskStore` exists. Every part should be checked; all current citations would still pass.
- **D3. Make the closed-vocabulary rule a test.** Fail a definition containing backticks,
  `taskfw/`, `task:<hex>`, ALL_CAPS names, or `__`. Land it after B5 (12 terms fail today).
- **D4.** Fail relations already implied by an `is-a` parent.
- **D5. (Optional)** Require `is-a` to join terms of the same form.

---

## 6. Suggested order

1. A1: small, a real bug, and it contradicts a stated guarantee.
2. A2: small, but changes response keys.
3. D1–D2: make the test cover what it appears to cover.
4. B + C in one ontology pass, with D3 as its gate.
5. A3–A5: renames and dead checks, when those modules are next open.

## 7. The one-line summary

The ontology is right about the domain but too long, and reading it against the code showed a
grade that can still be lost and three words the code uses in two senses.
