# Review: the MCP tool surface from grooming's side

**Date:** 2026-09-27
**Scope:** `.claude/skills/task-grooming/skill.md`, `taskfw/mcp_server.py` (`tasks__update`,
`tasks__context`, `tasks__grooming_accuracy`, activation hooks), `taskfw/context.py`,
`taskfw/risk.py`, `taskfw/dispatcher/{nudges,phase}.py`.
**Method:** read by hand, plus `tasks__grooming_accuracy(limit=40)` and read-only queries against
`~/.taskfw/tasks.db`. No code was changed.
**Follow-up task:** task:0b9de163 (groomed).

---

## 1. Verdict

Grooming works when it runs. Mostly it doesn't run. Out of 40 finished tasks, 11 were groomed. 37 of
the 58 surprises nobody predicted came from 17 of the ungroomed tasks. Among groomed tasks, 16 of the
48 graded risks were `wrong`, and nothing records why. The tools make both problems cheap to
ignore. The one nudge about ungroomed work fires only after implementation has started. The
grooming payload is also an untyped `dict`, so malformed or off-schema findings go in silently.

## 2. Data

| Measure | Value |
|---|---|
| Tasks examined / groomed | 40 / 11 |
| Risks | 50: 9 materialized, 23 avoided, 16 wrong, 2 ungraded |
| Predictive value | 0.67 |
| Missed surprises, groomed tasks | 21 |
| Missed surprises, ungroomed tasks | 37 (17 tasks) |

Stored grooming keys across all tasks: `risks` 232, `clarifications` 229, `prior_art` 224,
`hidden_assumptions` 220, `suggested_improvements` 219, `open_questions` 131, `last_run_at` 48,
`summary` 1, `prior_art_note` 1. The last three have no reader anywhere in the repo or skills.
One task's grooming is non-empty while all six finding fields are empty, so `is_groomed` counts it
as groomed with no findings.

## 3. Findings

- **G1. The ungroomed nudge fires too late.** `ungroomed_progress_nudge` (`dispatcher/nudges.py:122`)
  needs at least one ticked checklist item, so it fires after the work it was meant to come before.
  The moment before work is `tasks__set_active`, which the implementation skill calls when work
  begins. Not `tasks__create`: a new task is always ungroomed, so a nudge there would fire every
  time and tell the agent nothing.
- **G2. Grooming's shape isn't enforced, only described.** `tasks__update(grooming: dict)` accepts
  any keys. A typo is stored, never read, and still flips `is_groomed`. Following "delete the
  failure mode", the fix is a typed shape with one free-text escape-hatch field, not a validator.
- **G3. Grooming never reads its own track record.** The skill mentions
  `tasks__grooming_accuracy` only as something that counts against you. It never tells the agent to
  call it, so repo-specific patterns in `wrong` grades never reach the next pass.
- **G4. Lessons have no fallback read.** `tasks__context` returns at most 3 lessons, and they're
  the second section trimmed. The skill never mentions `task_memory__recall` or
  `task_memory__link`, so when lessons are cut, and when grooming confirms or disproves one, no
  step catches it.
- **G5. A `wrong` grade doesn't say why.** "Didn't happen", "one grep would have settled it" and
  "couldn't be falsified" are all stored as the same `wrong`. task:c0c5ff5f's pattern (4 of 5 wrong
  risks were "a test will break") was found by hand. An optional `wrong_because` would let
  `grooming_accuracy` group these automatically.
- **G6. Open questions don't say how to settle them.** A short hint would push grep-settleable
  questions into `clarifications`. This is the weakest finding. Measure after G3–G5 land before
  adding structure for it.

## 4. Recommended order

1. G3 + G4: skill text only, no code risk.
2. G1: one nudge on `tasks__set_active`, plus a test that `tasks__create` stays silent, plus
   ontology/concept entries (every nudge is a term since the 2026-09-26 review).
3. G2: needs a decision on legacy keys first (see task:0b9de163 open questions).
4. G5, then decide on G6 using the data.

## 5. Outcome (same day)

- **G1 fixed (f32fc4e).** A new nudge fires on `tasks__set_active` when the task is ungroomed.
  A test checks that `tasks__create` stays silent. The nudge has an ontology term and a
  concept_store entry.
- **G2 fixed (a4de189).** Grooming is a typed shape in `taskfw/grooming.py`, with `other` as the
  escape hatch. An off-schema key is refused on write. On read, legacy keys (`last_run_at`,
  `summary`, `prior_art_note`) are folded into `other`, not dropped. A blob with no findings no
  longer flips `is_groomed`.
- **G3 and G4 fixed (91a4c56).** Skill text only. The grooming skill now calls
  `tasks__grooming_accuracy` before it predicts. It uses `task_memory__recall` as the fallback when
  lessons are trimmed from `tasks__context`, and records a confirmed or disproved lesson with
  `task_memory__link`.
- **G5 fixed (91a4c56).** `wrong` grades take an optional `wrong_because`, and
  `tasks__grooming_accuracy` groups by it. The introspection skill and `05-introspection.md` ask
  for it.
- **G6 not done, on purpose.** §4 says to measure after G3–G5 land. They landed today, so there is
  nothing to measure yet. Revisit it once several groomed tasks have been introspected. Check
  whether open questions that one grep could settle still show up in `open_questions` rather than
  `clarifications`.
- **Found while fixing, not in this review (64a6433, task:adc1353b).** A grooming write replaced
  the whole payload, so grading `risks` alone erased every other finding. Writes now merge per key.
  An absent key keeps its value, and `[]` clears it.
- **Tests:** 690 passed, run after 71a16a2 re-stamped the models.
