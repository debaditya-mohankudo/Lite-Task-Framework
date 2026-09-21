# Review: tool behaviour, read against the ontology

**Date:** 2026-09-21
**Scope:** `taskfw/{task,risk,lifecycle,store,mcp_server,context,accuracy,scope}.py`,
`taskfw/dispatcher/*`, `taskfw/db/schema.py`, `ontology/task-domain.json`, read against each
other and against the 2026-09-14 review.
**Question asked:** review the code with the ontology; find issues and suggest enhancements.
**Method:** read by hand, then the MCP tool functions called directly against a scratch store
(`set_store(TaskStore(tmp))`), plus one read-only FTS query against the live store
(`~/.taskfw/tasks.db`, 1,860 tasks). Findings marked *verified* were reproduced that way; the
rest come from reading and are not proven.

**Side effect of the method:** the scratch run still wrote log lines to the live `logs` table,
because `SQLiteHandler` always targets `config.db_path()` (see C10). The table has 42 rows between
2026-09-21 15:51 and 15:53 UTC; some of them may come from the running server instead.
They were left in place.

---

## 1. Verdict

The 2026-09-14 code findings (A1–A5) all landed. Its ontology and test findings (B, C, D) are
untouched. This pass found four tool behaviours that either lose data silently or skip the
introspection reminder. The worst is search: on the live store it can only see tasks created
before 2026-08-01. The ontology would have made two of these visible if it named the link
between the checklist and status (O3).

---

## 2. Code findings

### C1. Search only ever re-ranks the oldest matches — `taskfw/store.py:search` (*verified, live store*)

The FTS branch fetches `LIMIT limit*4` rows with no `ORDER BY`, so SQLite returns them in rowid
order, and `_combination_score` re-ranks only that window. For the most recently updated
task, `_query_terms` matches **1,753 of 1,860** tasks; the 84 rows kept were all created between
2026-06-09 and 2026-08-01. This affects:

- `tasks__search` (limit 25, so the window is 100),
- `related_candidates` on `tasks__create` and `related` in `tasks__context`
  (`context.related` asks for `MAX_RELATED*4+1` = 21, so the window is 84; `_same_project` then
  filters that already-stale window).

Fix: `ORDER BY rank` before the `LIMIT`, or score every match (1,860 rows is cheap). A test
that inserts old filler rows before a strong newer match would pin it.

### C2. `tasks__update(resolution=[...])` resets every tick — `mcp_server.py:tasks__update` (*verified*)

`updated.resolution = [ResolutionItem(t) for t in resolution]` rebuilds every item with
`done=False`. A 3-item list with 2 ticked, updated to add a fourth item, reads 0/4 afterwards.
Nothing in the response says ticks were dropped. This is the same kind of loss the risk-grade
merge (`_keep_grade`) was built to prevent. Fix: keep `done` for an incoming item whose text
matches a current one, the same way `_keep_grade` keeps a grade.

### C3. Three ways to reach `done`, only one reminds you to introspect (*verified*)

| Path | `finish_nudge` | status event |
|---|---|---|
| `tasks__finish` | yes | if `reason` given |
| `tasks__check_item` last-item auto-finish | **no** | yes |
| `tasks__update(status="done")` | **no** | **no** |

`_finish_hook` is wired only onto `tasks__finish`. `finish_reminder_nudge` (on check_item and
update) stays silent once status is `done`. So the reminder to introspect is missing on the
auto-finish path, which is probably how most tasks close. That undercuts CLAUDE.md's
"Introspection got skipped because the work went well". Fix: send every close through
`_finish_task` (including update's `status="done"`), and emit `finish_nudge` whenever a call
moved the task to `done`.

### C4. A grooming update without `risks` deletes every ungraded risk (*verified*)

`tasks__update(grooming={"open_questions": [...]})` calls
`_merge_grooming_risks(current, None)`. Graded risks survive, but every ungraded risk is
treated as retracted, so `risks` becomes `[]`. Leaving the key out is not a retraction.
`_merge_grooming_risks`'s docstring gives that as the reason a graded risk is carried forward,
and the reason applies more strongly when the key is absent. Fix: when `"risks"` is not in the
incoming grooming, keep the current risks unchanged.

### C5. Status and checklist can disagree (*verified*)

Unticking an item on a `done` task leaves it `done` at 0/1. Ticking items on a finished or
abandoned task is allowed. Ticking the last item finishes a task, but unticking one does not
reopen it. Either refuse ticks on finished or abandoned tasks (one rule in `lifecycle`), or
state in `lifecycle`'s docstring that this is intended.

### C6. The self-parent check can never fail — `lifecycle.check_save`

`tasks__create` always uses a new id, and `tasks__update` has no `parent` argument, so no tool
can produce `task.parent == task.id`. It is the same kind of never-failing check as 09-14 A5.
Only `tests/test_lifecycle.py` reaches it. Delete it. If you later want tasks to be movable
under a different parent, add that to `tasks__update` and check for cycles, not just self.

### C7. The loop-debt reminder uses the server's working directory, not the task's scope — `mcp_server._loop_debt_hook`

The hook has the activated `task` in hand but calls `loop_debt(..., scope=derive_scope())`.
`Task.scope` is the recorded fact; `derive_scope()` is a re-derivation from the working
directory. Use `task.scope or derive_scope()`.

### C8. One project, two scopes (live data)

stock-tracker-mcp has 40 tasks under `git:github.com/.../stock-tracker-mcp` and 13 under
`path:/Users/debaditya/workspace/stock-tracker-mcp`. `_same_project` treats them as different
projects. `tasks__update(scope=)` can now correct the 13 rows and leaves an event behind each
correction.

### C9. Dead table definition — `taskfw/db/schema.py:128`

`ACTIVE_TASK_STACK` is not in `TABLES` and nothing references it. The comment explaining why
the table is left orphaned in old databases is worth keeping; the `Table` object is not.

### C10. Logging always goes to the default database

`TaskStore(path)` doesn't redirect logging, so any script that opens its own store still logs
to `~/.taskfw/tasks.db`. `log_conn()`'s docstring documents this, but it is still a trap for
scripts (this review ran into it). Setting `TASKFW_DB` avoids it.

---

## 3. Ontology findings

- **O1. `ActiveTaskPointer` contradicts itself.** Its definition says "keyed by working
  directory". Its `lifecycle` says "A Scope names at most one Task active". The A4 rename
  (`_scope` → `_workspace`) changed the code but missed this sentence. It should say
  workspace.
- **O2. Stale claim about the loop-debt reminder.** The `Nudge` definition and CLAUDE.md
  ("Advisory nudges" paragraph) say `loop_debt_nudge` rides only `tasks__set_active`. Since
  58a2028 it also rides `tasks__create`.
- **O3. Missing relation between the checklist and status.** Ticking the last `ResolutionItem`
  moves `TaskStatus` to `done`, and unticking does not reverse it. The ontology is silent on
  this, so it reads as though progress and status are independent. A relation such as
  `ResolutionItem drives TaskStatus`, with a note, would have exposed C3 and C5.
- **O4. Task-tracking text inside a definition.** `Risk` ends with "an open question tracked as
  its own task". That describes the process, not the term (the same kind of issue as 09-14 B5).
- **O5. Weak evidence on the grooming terms.** `BlastRadius`, `HiddenAssumption`,
  `OpenQuestion` and `ClaimConfidence` cite phrases in skill and methodology prose (e.g.
  `skill.md:blast radius`) or `GROOMING_TRIM_ORDER`. A substring check on prose passes almost
  regardless of what the file says, so `test_ontology.py` can't catch drift in these terms.

### Still open from 2026-09-14

B1–B7, C1–C5 and D1–D5 unchanged. In particular D1: `lifecycle_evidence` on six terms (Task,
TaskStatus, ActiveTaskPointer, MemoryKind, MemoryRelationship, GroomingGrade) is still never
parsed. C5: `Decision` still has form `attribute` while being `is-a Event`, which is a `record`.

---

## 4. Suggested order

1. C1–C4: small, and each one loses data or hides the introspection reminder.
2. O1, O2 together with 09-14 D1/D2.
3. C5, C6 as a single decision about which rules belong in `lifecycle`.
4. C7–C9 when those modules are next open; C8 is a data correction, not code.
5. The remaining ontology items (09-14 B and C) in one pass.

## 5. The one-line summary

The rules are all in one place, but four tool paths can still lose data or skip the
introspection reminder, and search can't see any task created after August 1.
