# commit, in rule form (experimental draft)

Not a live skill. Source: `task-framework/.claude/skills/commit/SKILL.md`, as read on 2026-11-26.
Nothing checks that the two agree.

## Preamble (shared by all skills)

1. `except` is indented under one `do` or `never` line and overrides that line only.
   Every other line of the rule still holds.
2. Between rules, `never` beats `do`, then the narrower `when` wins. A remaining tie is a bug.
3. `when` carries to following rules in the same block until restated; a blank line ends the block.
   A block with no `when` always applies.
4. A `term` is defined once per skill.
5. `proc` is a literal command sequence a rule refers to by name.
6. `example` shows one correct instance. It adds no obligation; where it disagrees
   with a rule, the rule wins.

## Terms

```
term  D      = pending diff
term  TF     = the repo is task-framework
term  SUITE  = the repo's full test suite (TF: uv run pytest -q)
term  MSG    = message file in the scratchpad
term  STALE  = packages tests/test_model_provenance.py names after commit 1
```

## Rules

```
rule  scope
  do      apply citation, message shape and commit link wherever taskfw runs
    except  a step's files are absent from the repo: skip that step
  check   judgment

rule  simplify-first
  when    D && !(simplify ran on this exact D this session, nothing left uncommitted since)
  do      skill:simplify, apply its findings
  before  suite, commit
  why     fixes land in the same commit
  check   judgment
rule  serial
  do      run simplify's four review agents one at a time
  why     user preference, saves budget
  check   judgment
rule  no-regression
  never   apply a simplification that regresses behaviour or breaks a test
    except  one would: skip only that one, apply the rest
  nuance  verify against the suite run, not by inspection alone
  check   SUITE (only what tests cover)

rule  suite
  when    D
  do      run SUITE
    except  TF && D touches only ontology/task-domain.json && no .py: run tests/test_ontology.py
  before  commit
  never   pick your own subset, such as "the tests I think I touched"
  why     coverage tests bind modules to concepts and the model
  check   judgment
rule  red-stops
  when    SUITE fails
  do      say so and stop
  never   commit, or call a failure pre-existing without checking
  check   judgment

rule  concept-store
  when    before commit
  do      concept__store_status(repo); show any warning verbatim;
          stage concept_store/ if its edits belong to this task
    except  tool unavailable: git status --porcelain --untracked-files=all -- concept_store/
            and treat any output as the warning
  nuance  advisory, never stops the commit; no warning means say nothing
  nuance  tasks__active reports stale: true -> mention a restart restores the tool
  why     concept__upsert writes the store but nothing stages it
  check   judgment

rule  cite
  when    commit
  do      first line starts "task:<id> Subject", id from tasks__active
    except  no active task: ask which task
  never   invent an id, or cite a task the work does not belong to
  why     a false edge is worse than a missing one
  example task:1a2b3c4d The gate reads the message file — made-up id and subject
  check   hooks/gates.py denies a commit with no task:<id> (presence only, not position or truth)

rule  message
  do      build with tasks__format_commit_message;
          append the session's attribution trailer; write MSG; git commit -F MSG
  never   copy a model name from the skill; use heredocs or -m chains
  nuance  subject: the claim, em-dash, present tense, what became true, no trailing period
  nuance  body: why, not what; load-bearing design points; state failures directly,
          not by reference to what replaced them
  example tasks__format_commit_message(task_id="<id>",
              subject="Subject — the claim, in one line",
              body="Why this exists: what was true before, what is true now.\n\n"
                   "Load-bearing design points that would otherwise look arbitrary.")
  example file written to MSG:
              task:<id> Subject — the claim, in one line

              Why this exists. ...

              Co-Authored-By: <the session's attribution trailer>
  check   tool rejects empty or multi-line subject and unknown task; rest judgment

rule  link
  when    after commit
  do      confirm the sha in tasks__context(id).commits
    except  missing: tasks__add_commit(id, sha, repo)
  nuance  idempotent; python -m taskfw.backfill can re-derive links later
  check   taskfw.commit_hook links; confirmation is judgment

rule  two-pass
  when    TF && D touches a modelledPath or models/*.sysml
  do      proc:two-commits
    except  provenance test passes && nothing under models/ is left: stop at one commit
  never   predict the hash, or amend
  check   tests/test_model_provenance.py

rule  split
  when    choosing commit boundaries
  do      one commit per idea
    except  a split makes an intermediate commit fail its tests: commit together, say why in the body
  why     tests/test_concepts.py binds modules to concepts and the model
  check   judgment

rule  exclude
  never   commit scratchpad files, generated artifacts, caches, .venv,
          or deployed copies whose source is in another repo (fix the source)
  check   judgment

rule  report
  when    done
  do      report subject, files, test result as a number, and anything left out with why
  check   judgment
```

## Procedures

```
proc  two-commits
  # commit 1: everything except models/
  git add -A
  git restore --staged models/
  git commit -F MSG
  git rev-parse HEAD                                    # -> SHA
  uv run pytest -q tests/test_model_provenance.py       # names STALE

  # commit 2: held-back .sysml edits and STALE packages
  re-read each STALE package against commit 1; fix drifted prose
  set derivedFromCommit = "<SHA>", derivedAt = "<today>"
  message: "Re-stamp <packages> after <change>"; body: which, why, re-read confirmed or fixed
  git add -- models/
  git commit -F MSG
  uv run pytest -q tests/test_sysml.py tests/test_model_provenance.py   # report the number
  confirm both commits are linked
```

Commit 2 touches no modelled path, so its stamp stays correct without prediction.
