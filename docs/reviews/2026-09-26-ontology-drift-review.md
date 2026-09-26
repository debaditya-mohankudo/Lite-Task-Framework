# Review: code against the ontology, after the 2026-09-21 fixes

**Date:** 2026-09-26
**Scope:** `ontology/task-domain.json`, `tests/test_ontology.py`, `taskfw/{lifecycle,mcp_server,task,memory,risk}.py`,
`taskfw/dispatcher/{nudges,phase}.py`, `taskfw/db/schema.py`, `CLAUDE.md`. Read against the
2026-09-21 review.
**Method:** read by hand, plus a script that listed terms, evidence and relations. Nothing was run
against a store.

---

## 1. Verdict

The 2026-09-21 code findings C1–C4 landed in 714dcce. Its ontology findings (O1–O4) and the code
items C6, C7 and C9 did not. Three new drifts turned up: 5 of the 7 nudges have no term, the
derived Phase has no term, and `EventKind`'s legacy value is described as if it were live. The
cause is the same each time. `test_ontology.py` only checks what the ontology cites, never what it
leaves out, and it does not compare the closed vocabularies with the code.

## 2. Ontology findings

- **N1. Most nudges have no term.** `dispatcher/nudges.py` defines seven: `introspection`,
  `finish`, `finish_reminder`, `stale_memory`, `ungroomed_progress`, `loop_debt` and `task_debt`.
  Only `IntrospectionNudge` and `LoopDebtNudge` are terms. `finish_nudge` enforces "don't skip
  introspection", yet it isn't in the vocabulary.
- **N2. No term for Phase.** `dispatcher/phase.py:task_phase` computes groomed, implemented and
  introspected on read, and `tasks__phase` returns it. It is the domain's clearest computed-on-read
  value, and it has no term.
- **N3. The claim that loop debt only rides `tasks__set_active` is stale (09-21 O2).** It appears in
  `Nudge`, `LoopDebtNudge` and `CLAUDE.md`. `_loop_debt_hook` also fires on `tasks__create`.
- **N4. `ActiveTaskPointer` contradicts itself (09-21 O1).** It is "keyed by working directory",
  but its lifecycle says "A Scope names at most one Task active".
- **N5. No link between the checklist and status (09-21 O3).** Ticking the last `ResolutionItem`
  moves `TaskStatus` to `done`, and unticking does not reverse it.
- **N6. `Decision` has form `attribute` but is `is-a Event`, and Event is a `record` (09-14 C5).**
- **N7. `hooks_event` is described as a live kind.** `task.py` says it is a legacy value that only
  the one-time importer wrote. The `Event` definition lists it next to the live kinds.
- **N8. Task-tracking prose in `Risk` (09-21 O4).**

## 3. Code findings

- **K1. The self-parent check can never fail (09-21 C6).** No tool can make `parent == id`
  (`lifecycle.py:184`). The `Task` definition repeats the rule, so the ontology supports a check
  that guards nothing.
- **K2. The loop-debt hook re-derives scope (09-21 C7).** `_loop_debt_hook` has the task in hand
  but passes `derive_scope()`. `Scope`'s lifecycle says scope is never recomputed on read.
- **K3. The `ACTIVE_TASK_STACK` table object is dead (09-21 C9).** The ontology says "not a stack".

## 4. Test findings

- **T1. Closed vocabularies are only prose.** `TaskStatus`, `EventKind`, `MemoryKind`,
  `MemoryRelationship` and `GroomingGrade` describe their values in text. Nothing compares them with
  `TASK_STATUSES`, `EventKind`, `MEMORY_KINDS`, `MEMORY_RELATIONSHIPS` or `GROOMING_GRADES`.
  A structured `values` field checked against the imported constant would have caught N7.
- **T2. Nothing checks for missing terms.** A test that every public `*_nudge` in `nudges.py` is
  cited by some term would have caught N1.
- **T3. `lifecycle_evidence` is never parsed (09-14 D1).** Still open. Not addressed in this pass.

## 5. Order

1. K1, K2, K3: small code changes that make the ontology's claims true.
2. N1–N8 in one edit to `task-domain.json`, plus the sentence in `CLAUDE.md`.
3. T1 and T2 in `tests/test_ontology.py`.

## 6. The one-line summary

The code follows its rules, but the ontology leaves out most nudges and all of Phase, and no test
could notice, because the tests only check what the ontology cites.

## 7. Outcome (same day)

- **K1 fixed.** The check is deleted from `lifecycle.check_save`, along with its test. The logging
  test now uses the epic-parent denial. `02-create.md`, the task-create skill,
  `requirements.sysml:ParentRuleRequirement` and the `Task` term now describe self-parenting as
  unrepresentable, not checked.
- **K2 fixed.** `_loop_debt_hook` now uses `task.scope or derive_scope()`.
- **K3 not done, on purpose.** concept_store's entry for `db/schema.py` records that the `ACTIVE_TASK_STACK`
  constant stays because of the additive-only migration rule. This review had missed that.
- **N1–N8 fixed.** The new terms are `FinishNudge`, `FinishReminderNudge`, `UngroomedProgressNudge`,
  `StaleMemoryNudge`, `TaskDebtNudge` and `Phase`. `ResolutionItem relates TaskStatus` is added,
  with a note that the link only goes one way. `Decision` is now form `record`. `hooks_event` is
  marked legacy. The `Risk` process prose is removed. The `set_active`-only claims in the
  ontology, `CLAUDE.md` and the `nudges.py` docstrings are corrected.
- **T1 and T2 added.** `TestClosedVocabularies` compares the `values` of five terms with the
  imported constants. `TestCoverage` requires every `*_nudge` in `nudges.py` to be cited by some
  term. T3 is still open.
- **Tests:** 660 passed and 3 failed. The failures are
  `test_model_provenance` stamps on `mcp_interface`, `requirements` and `task_framework_system`.
  They were already failing at HEAD before this change, since 891b0b7 touched modelled code
  without re-stamping. `requirements.sysml` was edited here, but its stamp was not bumped: the
  re-read that the test asks for belongs to the 891b0b7 change, not to this one.
