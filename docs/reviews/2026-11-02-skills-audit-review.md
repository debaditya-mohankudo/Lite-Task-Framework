# Review: the seven skills against each other, the methodology, and the tool surface

**Date:** 2026-11-02
**Scope:** `.claude/skills/{task-create,task-framework,task-grooming,task-implementation,task-introspection,commit,task-skills-audit}/skill.md`,
`docs/methodology/*.md`, `taskfw/mcp_server.py`, `taskfw/accuracy.py`, `taskfw/introspection.py`,
`taskfw/dispatcher/chassis.py`.
**Method:** the `/task-skills-audit` procedure, read by hand. `tests/test_skills.py` run first. Each
cross-skill and skill↔code claim below was checked against the current source or `~/.taskfw/tasks.db`,
not taken from the prose. No code or skill was changed.
**Follow-up task:** task:c2ba07ec fixed S1–S12. The `model: sonnet` note is left for the user to decide.

---

## 1. Verdict

The mechanical layer is clean: `tests/test_skills.py` passes 53/53, so every tool a skill names exists.
The drift is in the prose the test can't read. The worst case is `task-framework`, the entry-point
skill. It describes two hooks that don't exist (S1, S2), and three other skills contradict it on one
of them. The skills have also gained operational steps the methodology docs never got (S4), so the
"doc owns the reasoning" split now holds for grooming but not for implementation or introspection.
Five of the seven `updated:` stamps are older than the skill's last commit, so the field can't be
trusted to say when a skill last changed (S9).

## 2. Data

| Measure | Value |
|---|---|
| Skills checked | 7 (the five lifecycle skills, `commit`, `task-skills-audit`) |
| `tests/test_skills.py` | 53 passed |
| Registered tools (`tasks__`, `concept__`, `task_memory__`) | 37 |
| Tools no skill mentions | 8: `tasks__get`, `tasks__search`, `tasks__phase`, `tasks__open_in_kitty`, `tasks__open_plain_tab`, `task_memory__get`, `concept__modules`, `concept__delete` |
| `updated:` older than last commit | 5 of 7 |

| Skill | `updated:` | Last commit |
|---|---|---|
| commit | 2026-08-31 | 2026-10-26 |
| task-create | 2026-08-08 | 2026-09-26 |
| task-framework | 2026-08-01 | 2026-08-31 |
| task-grooming | 2026-10-26 | 2026-10-26 |
| task-implementation | 2026-08-08 | 2026-10-27 |
| task-introspection | 2026-09-01 | 2026-10-31 |
| task-skills-audit | 2026-08-01 | 2026-08-04 |

## 3. Findings

### Vestigial machinery

- **S1. `task-framework` says a hook records commit links. No such hook exists.** Step 6: "Where the
  PostToolUse hook is installed the link is recorded automatically". `task-implementation` (Finish),
  `commit` ("There is no hook watching for this") and `task-introspection` Step 2 ("`tasks__add_commit`
  is a manual, unhooked step") all say the opposite. The repo has no commit-capture hook, and
  `~/.claude/settings.json` wires none. Loop memory `raw-git-commit-skips-tasks-add-commit` has 3 links:
  this is the mistake that has already cost three tasks their commit links. The entry-point skill is
  the one teaching the wrong model. It also never points at `/commit`.
- **S2. `task-framework` says a hook prints an active-task pointer.** Line 19: "Where hooks are
  installed, a one-line pointer names the active task." `docs/methodology/README.md` says there is "no
  host-side pointer that reminds you, on purpose". `mcp_server.py:172-180` records that both attempts
  at one (task:8be768df, task:1c8f0815) were removed and says "Do not re-add a periodic active-task
  reminder in any form."
- **S3. `task-introspection` Step 8 cites a `symbol_resolver.py` this repo doesn't have.** The
  sanctity check depends on `parse_citation()` / `resolve_symbol()` from task:85e6001f / task:ba5d5aed.
  Neither the file nor the functions are in this repo. The step is worded as conditional ("If the repo
  has…"), so it is harmless here. It is a step ported from another repo that always skips in this one.

### Skill ↔ methodology drift

- **S4. Operational steps exist only in the skills.** By the skills' own rule, the reasoning belongs in
  the doc. Now in a skill and missing from its doc:
  - `task-implementation`: "Read the logs before you close" (about 30 lines of reasoning, task:1f400ecf)
    and "commit, then `tasks__add_commit` yourself". `04-implementation.md` has neither.
  - `task-introspection`: Step 2's commit-link diff, the `tasks__logs` trace, all of Step 8 (concept
    store, `remove=`, the sanctity check), and `stale_knowledge_flagged` in the report. `05-introspection.md`
    has none of them, even though `IntrospectionReport` accepts that field.
  - `task-create`: the Scope section and the concept lookup. `02-create.md` has neither.
  - `commit` points `doc:` at `04-implementation.md`, which says nothing about committing. All of that
    skill's reasoning lives in the skill.
- **S5. `04-implementation.md` contradicts the code. The skill is correct.** The doc says "Switching
  away from a different active task is refused unless confirmed". The `tasks__set_active` docstring
  says "Nothing here is destructive, so there is no confirm to pass", and the skill agrees with it.
  The doc is stale.
- **S6. `task-create` leaves out `related_candidates`.** `02-create.md` tells the agent to review the
  overlap candidates `tasks__create` returns ("don't ignore it unseen"). The skill, the one agents
  actually load before creating, never mentions them.

### Cross-reference mismatches

- **S7. Grooming scopes `tasks__grooming_accuracy`. Introspection doesn't.** `task-grooming` Step 1
  passes `scope="<scope from the bundle>"`. `task-introspection` Step 4 and `05-introspection.md` call
  `tasks__grooming_accuracy(limit=25)`, which is global. In a shared `tasks.db`, the "grade the grader"
  step reads other repos' grades as this repo's pattern. The code doesn't say which is intended, and
  this review doesn't pick one.
- **S8. The two skills order commit and finish differently.** `task-framework` puts commit (step 6)
  before finish (step 7). `task-implementation` Finish calls `tasks__finish` first, then says "Commit
  with `task:<id>`…". Both work. A reader following both gets two different orders.
- **S9. `updated:` isn't maintained.** See the table. `test_skills.py` checks only that the field is
  present. `updated:` doesn't drift from anything this review can check, but a reader will believe it.
  This is a field derived from git that is stored by hand, which is the case "delete the failure mode"
  covers: drop it, or derive it from git.
- **S10. `task-implementation` cites a line that moved.** It points at "chassis.py:105" for the
  `tool=<name> ERROR` log line. That line is now `taskfw/dispatcher/chassis.py:116`, and the path was
  never given. The claim itself still holds: the line logs at INFO.

### Unreferenced tools

- **S11. `tasks__search` and `tasks__phase` have obvious places in the skills and are never named.**
  Grooming's duplicate check ("Consult the task graph rather than search hits alone") depends on the
  bundle's neighbours. When those are trimmed or thin, `tasks__search` is the direct fallback, the same
  way `task_memory__recall` is for `lessons`. `tasks__phase` reports where a task stands in the loop.
  That is the question `task-framework`'s Resuming section answers by having the agent read
  `tasks__context` by eye.
- The other six have no edge, and that is fine: `tasks__get` (the bundle covers it), `task_memory__get`
  and `concept__modules` (utilities), `concept__delete` (the skills forbid deleting a concept
  unilaterally), and the two kitty tools (optional, per CLAUDE.md "A tool with no skill edge is fine").
  `tasks__context(verbosity="summary")` is documented in `01-framework.md` and appears in no skill. That
  is minor.

### The audit skill itself

- **S12. `task-skills-audit` is out of date with the repo.** It describes "a chain of five skills".
  `commit` is a sixth that it doesn't name, and `commit` is the one carrying S1's correct version. Its
  tool-enumeration grep matches only `tasks__|concept__`, so the six `task_memory__*` tools are never
  checked for skill coverage. It was last touched 2026-08-04.

### Not drift, worth knowing

- **`task-implementation` pins `model: sonnet`** in its frontmatter. It is the only skill that does, so
  implementation runs on a different model from the session that groomed the task. That may be
  intended. It is recorded nowhere in the skill or the doc.

## 4. Suggested order

| # | Fix | Why first |
|---|---|---|
| S1, S2 | Delete the two hook sentences from `task-framework`; point step 6 at `/commit` | The entry point teaches a model three other skills contradict, and the failure it causes has already happened 3 times |
| S12 | Update the audit skill: six skills plus `commit`, include `task_memory__` in the grep | So the next audit catches what this one found by hand |
| S7 | Decide whether introspection's accuracy call is scoped | Changes what the grader reads |
| S4, S5, S6 | Move the skill-only reasoning into the docs; fix the stale confirm line | Restores the doc/skill split |
| S9 | Drop `updated:` or derive it | Delete the failure mode rather than police it |
| S3, S8, S10, S11 | Small wording edits | Low cost, low impact |
