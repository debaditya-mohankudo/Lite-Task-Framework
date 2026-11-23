# Review: skills and overall architecture, read against the loop's own numbers

**Date:** 2026-11-23
**Scope:** `CLAUDE.md`, `ontology/task-domain.json`, `README.md`, all seven
`.claude/skills/*/skill.md`, the tool surface in `taskfw/mcp_server.py`,
`taskfw/kitty/launcher.py`.
**Method:** read by hand. Ran `uv run pytest -q`, `tasks__grooming_accuracy(limit=50)`
scoped to this repo, and `tasks__list(limit=60)`. Claims about code were checked
against source; claims about the loop against the store. No code or skill was changed.
**Follow-up task:** none filed yet.

---

## 1. Verdict

The core design holds up: values are computed on read, the vocabularies are closed,
and "omitted" is kept separate from "absent". The real problems are that the loop is
not run to the end (committed tasks stay open and are never introspected, and the
debt counter can't see them), and that six parallel descriptions of the system each
need keeping in sync, and the costliest one has left `main` red.

## 2. Data

| Measure | Value |
|---|---|
| `uv run pytest -q` | 2 failed, 923 passed, 1 skipped |
| Failures | `test_model_provenance.py` for `foundation.sysml` and `requirements.sysml`, stale since `cab2a3b` (task:8b0561d1) |
| Tasks examined / groomed (this scope) | 50 / 31 |
| Risks: total, materialized, avoided, wrong, ungraded | 106, 16, 51, 36, 3 |
| `wrong_because` | did-not-occur 19, settleable-by-grep 8, unstated 8, unfalsifiable 1 |
| Predictive value | 0.65 |
| Missed surprises (groomed tasks / ungroomed tasks) | 44 / 12 |
| `skipped_introspection` | 1 (`670ac27a`), plus 2 unscoped not counted |
| `self_report_disagreements` | 2 (`eb308ffc`, `0b9de163`) |
| Open tasks in `tasks__list` | hit the 60-row limit, across many repos |
| Registered MCP servers in `~/.claude.json` | `find_cli`, `taskfw` |

## 3. The loop is leaking

**L1. Committed tasks are left open, so they're never introspected.** For example,
task:6de9f540 has commit `ef35f0e` and still reads 0/3 open. task:8b0561d1 is the
commit at HEAD and is open at 5/10. `tasks__grooming_accuracy` and `loop_debt`
only look at *finished* tasks, so the report shows 1 skipped introspection while
these stay invisible. This is the drift CLAUDE.md names as "the one that actually
happens", and right now nothing counts it. A "has commits but still open" signal
or nudge would close the gap.

**L2. The follow-up check on grooming is two weeks overdue.** task:63638782
("re-check grooming accuracy… about 2026-11-09") is still open at 0/3. `did-not-occur`
is still the largest `wrong` bucket (19 of 36). That is the safety-belief pattern
task:25ab1953's skill change was meant to cut. Whether those 19 come before or
after that change is what task:63638782 exists to answer. 8 `wrong` grades give no
reason.

**L3. Grooming misses more than it predicts.** There were 44 missed surprises across
31 groomed tasks, against 16 risks that materialized. By the skill's own reading
(repeated surprises mean grooming asks too few questions), the signal is "ask more",
not "ask sharper".

**L4. The store is one global pool with no project filter on listing.** `tasks__list`
and `tasks__search` take no `scope`. A bare `/task-grooming` starts from
`tasks__list()`, which here mixes this repo's tasks with Riemann, stock-tracker and
cli-graph tasks, most at 0% progress. The README section "Multiple projects" still
says "there's no built-in filtering by workspace", which predates `scope`
(task:85f6fef0 "Fix stale README and docs" is open).

## 4. Architecture

| # | Finding | Evidence | Why it matters |
|---|---|---|---|
| A1 | **The suite fails on main** | §2; the last commit was made under a cli-graph-led task and skipped the two-commit re-stamp | The provenance check catches drift after the fact rather than making it impossible, and it fails by design between commit 1 and commit 2. It is the costliest of the drift checks, and it gets skipped when work is driven from another repo. |
| A2 | **Six descriptions of one system** | CLAUDE.md, ontology (817 lines), concept store (76 concepts, 2172 lines), SysML models, `mcp_tools/tool-graph.json`, methodology docs plus skills | Each has its own drift test and every change pays for all of them. Worth asking which of them actually gets read at the moment a decision is made. |
| A3 | **The rule that carries risk grades forward lives in the MCP layer** | `_merge_grooming_risks`, `_keep_grade`, `_keep_ticks` in `mcp_server.py` (1518 lines); every `store().save` caller is in that file | Nothing diverges today. A second writer (a CLI, backfill growing a write path) would skip the merge and lose grades, which is the "rule a caller can reach around" case. These belong in `risk.py` / `grooming.py`. |
| A4 | **The kitty add-on is about as big as the core** | ~1.4k lines of code, ~1.8k lines of tests, ~40% of the README | It is optional and only adds things, as required. It still makes up a large share of a repo whose stated asset is the method. It could become its own package. |
| A5 | **Task tabs never get the slimmed server list** | `taskfw/kitty/launcher.py:69` `SLIM_MCP_DEFAULT = ("taskfw", "claude-hooks")`; `claude-hooks` is no longer registered | Under the README's own rule (a named server missing means every server), every task tab falls back to the full set. The README and the `mcp=` log line both still describe the slim set. |

**Ontology.** The definitions read more like a change log than a glossary. They carry
task ids, before-and-after history and repeated explanations (the "a tally is not a
push" argument appears in `Nudge`, `LoopDebtNudge` and its relation note). Three
things are filed in the wrong place:

- `Optional` and `Additive` are principles, not domain terms, and `part-of KittyTab` is a forced link.

- `KittyTab` sits under Task Tracking but is a host convenience.

- `SkillInvocation` sits under Loop Memory, which the ontology itself defines as
  "MemoryRecords and the closed vocabularies on them".

## 5. Skills

| # | Skill | Issue |
|---|---|---|
| S1 | `task-skills-audit` | It has drifted itself. It still lists `updated` as required frontmatter, but `tests/test_skills.py` dropped it (task:c2ba07ec). It checks a "byte-identical-text rule" that is now id-keyed merge plus normalised-text recurrence. It runs `.venv/bin/python` rather than `uv run`. Its lifecycle diagram puts `task-framework` after `task-create`. |
| S2 | all lifecycle skills | Every step calls `tasks__log_skill_invocation`, roughly 40 calls per loop pass. Only introspection step 2 reads them, and nothing tallies them. Either something should use them (e.g. "groomed without step 3") or they should be cut down to one per skill. |
| S3 | `task-grooming` | Step 3 is mostly argument, with task ids and baseline counts ("12 of 34…"). By the audit skill's own split ("the doc owns the reasoning"), that belongs in `docs/methodology/03-grooming.md`. The three-way blast-radius split itself is the strongest guidance in the set. |
| S4 | `task-implementation` | Its frontmatter has `model: sonnet`, so invoking it quietly drops off the session model. Already flagged in the 2026-11-02 review as left for the user to decide, and still unresolved. |
| S5 | `task-introspection` | The step 8 "sanctity check" depends on `symbol_resolver.py`, which this repo doesn't have. It's dead text here. |
| S6 | `commit` | Claims to be repo-agnostic, but half of it covers task-framework-only steps. It makes a four-agent `simplify` run mandatory before every commit, and says running them in sequence "saves budget", which it doesn't; it only lowers peak usage. That's heavy for small commits. |

## 6. What to do first

1. Re-stamp the two SysML packages, or make the provenance check advisory, so `main` passes again (A1).

2. Make "has commits, still open" show up in the loop-debt count, then finish or abandon the open tasks that already have commits (L1).

3. Add a `scope` filter to `tasks__list` and `tasks__search`, with the current project as the default (L4).

4. Fix `SLIM_MCP_DEFAULT` (A5) and the audit skill's stale checklist (S1).

5. Run the overdue check in task:63638782 with the numbers in §2 (L2).
