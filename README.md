# Lite Task Framework - For the SOLO Developer

A lightweight task framework for Claude Code — persistent task tracking, memory injection, and structured decision logging via Claude hooks.

> **macOS only** — requires Claude Code, uv, Ollama, and iCloud Drive. See [setup](docs/setup.md) for prerequisites.




---

## The perspective

Jira was the right idea. But it was always human-operated and AI-opaque. Now agents can close that gap natively.

Jira was the original development graph. It worked for teams. Epics, tasks, subtasks — every piece of work traced to a reason.

But Jira content is human-readable, not agent-readable. It breaks the moment an AI tries to operate on it without the right tooling around it.

Jira does expose MCP tools now. The graph can technically be agent-operated. But it's heavyweight — the infrastructure, the licensing, the setup — built for organisations, not for a solo developer running an AI-assisted workflow.

The real unlock is agents that create the full epic graph from a requirement, evaluate each item, and execute — with `task:<id>` in every commit, tying every change to a coherent piece of work. Nothing unattributed. Nothing untraceable.

That's not a workaround for bad process. It's the natural evolution of what Jira was always trying to do — lightweight, native, and built around how AI-assisted development actually works.

---

## New here?

```text
/onboarding
```

Run this in Claude Code after cloning the repo. It detects your OS, checks prerequisites, walks you through hooks and MCP server registration with your real paths filled in, and verifies the setup — one step at a time.

---

## Task framework — it's just skills, nothing to configure

There's no separate task engine running underneath this. The whole framework is a handful of markdown files that Claude reads and follows, calling the same tools you could call yourself. When you start a task, Claude isn't invoking some hidden state machine — it's activating the task (so context gets injected automatically every turn) and then working through three phases, each one a skill: groom it first, work it with a steady head, retrospect on it after.

Say what you want done, and mention the framework:

```text
migrate the auth module to use the new token schema  /task-framework
```

Claude proposes a split if the work has real phases, creates the tasks, and grooms the first one before touching any code — pulling in related past work and flagging gaps (a missing decision, a file that another task already owns) while it's still cheap to fix:

```text
This touches 3 areas — proposing subtasks:
  1. Audit current token usage across auth module
  2. Replace legacy token calls with new schema
  3. Update tests and integration points

Create as subtasks under a parent epic?
```

You confirm, and it activates the first one:

```text
task:4a2c done  →  Audit current token usage
task:7f1e active  →  Replace legacy token calls with new schema

Tracking turns and tools. Say "task:7f1e done" when finished.
```

From here Claude works the same way it always would — read, edit, test — except now a fixed north-star stays pinned in its context for the whole task ("keep the objective in focus, prefer the smallest next step, finish decisively rather than optimizing endlessly"), so a long task doesn't quietly drift into exploring forever. A load-bearing decision along the way gets logged explicitly:

```text
use opaque tokens stored in Redis rather than stateless JWTs  /log-decision
```

```text
Decision logged to task:7f1e: "Chose opaque tokens over JWT — avoids key rotation
complexity on short-lived sessions; Redis eviction handles expiry"
```

That survives context compression and reappears under `## Task decisions` every subsequent turn — Claude never asks why again. Commit, close, done:

```text
task:7f1e done
```

```text
task:7f1e closed — Replace legacy token calls
epic:4a1b closed — Migrate auth module to new token schema  ✓
```

And when it's closed, the retrospective isn't optional busywork — it's Claude asking itself what would make the *next* task like this one easier: were there decisions worth logging that got missed, is any memory now stale, is there a pattern worth remembering next time.

**How tasks are actually processed** — the checkpoint fields, the PostToolUse bridge that wires `tasks__set_active` into context injection, the Execution Contract, exactly what each of the three skills does — is written up in full: [Task Framework](docs/arch/task_framework.md).

---

## Gates — hard stops before anything irreversible

Gates are the one part of this system that isn't a suggestion. They sit in `PreToolUse`, before a tool call executes, and they deny it outright if a prerequisite wasn't actually satisfied — no relying on Claude's own judgment or its in-context memory of "yeah I already checked that."

The clearest example: sending an iMessage. The gate requires that `contacts__search` was actually called recently, *and* that the name searched for shows up in what you asked for — so a stale or hallucinated contact lookup can't slip a message to the wrong person:

```text
contacts__search(name="Alice")
imessage__send(recipient="+1-555-...", message="running late, be there in 10")
```

```text
✓ ALLOW — contacts__search found for 'Alice', 'Alice' present in prompt → message sent
```

Skip the search, or search for a different name than the one you actually asked about, and it's denied before the message ever goes out:

```text
Blocked: imessage__send — contacts__search was called for 'Bob' but that name does not
appear in the current or previous prompt. Search for the intended recipient first.
```

Deleting mail works the same way, just simpler — no name to double-check, just proof you actually read it first:

```text
mail__read(...)
mail__delete(message_ids=[...])
```

```text
✓ ALLOW — mail__read found within the last 120s → deleted
```

Both of these are declared entirely in a YAML config, not Python — adding a gate for some other tool in another repo is a config edit, not a code change. Task and git-commit gates work the same underlying way but need database access, so they stay as small Python classes instead.

**How the gate mechanism actually works** — the config schema, the internal-vs-external split, the full current gate list, how to add your own — is written up in full: [Gates](docs/arch/gates.md).

---

## claude-hooks, briefly

Underneath the task framework and the gates is a small FastAPI server that intercepts all four Claude Code hook events (`UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `Stop`) and runs them through one LangGraph pipeline. It injects relevant memories and past task context into every prompt, tracks which MCP tools get used so it can recommend the right one next time, and keeps all of that state durable across restarts in a single SQLite-backed checkpoint — so nothing above (tasks, gates, memory) depends on Claude's own context window to stay coherent.

**The full architecture** — components, design decisions, database inventory, the graph topology itself: [Architecture](docs/ARCHITECTURE.md).

---

## Mid-session

### Pausing gracefully

```text
/pause
```

```text
Paused. Completed: middleware updated, tests passing.

Pending (saved to task:7f1e):
- Update the logout endpoint to revoke Redis tokens
- Add token TTL config to settings.py

Waiting for your input.
```

Claude saves the pending work to the task body and stops — no half-finished reasoning, no context dropped.

### Resuming next session

```text
continue task:7f1e
```

```text
Resuming task:7f1e — Replace legacy token calls with new schema

Pending from last session:
- Update logout endpoint to revoke Redis tokens
- Add token TTL config to settings.py

Starting with logout endpoint...
```

---

## Skills

Skills are listed in the order you'd use them across a real session.

### Setup

| Skill         | What it does                                                                  |
| ------------- | ----------------------------------------------------------------------------- |
| `/onboarding` | Interactive setup guide — clone, prereqs, hooks, MCP server, smoke test       |

### Starting your day

| Skill                    | What it does                                                                        |
| ------------------------ | ----------------------------------------------------------------------------------- |
| `/what-am-i-working-on`  | Show recent prompts, tool calls, and activated tasks — your Monday-morning restore  |

### Task lifecycle

| Skill                  | What it does                                                                                    |
| ---------------------- | ----------------------------------------------------------------------------------------------- |
| `/task-framework`      | Start a tracked task — assesses complexity, proposes subtasks, activates the first              |
| `/task-create`         | Create Jira-style issues — epic / story / task / bug / subtask with hierarchy and parent links  |
| `/task-grooming`       | Pre-work audit — finds related tasks, injects relevant memories, flags gaps before you start    |
| `/task-implementation` | Behavioral guide for the work itself — smallest next step, validate early, finish decisively    |
| `/task-introspection`  | Post-task retrospective — surfaces unlogged decisions, stale memories, encodes learnings        |

### Mid-task

| Skill                | What it does                                                                               |
| -------------------- | -------------------------------------------------------------------------------------------|
| `/pause`             | Finish the current action, save pending intent to the active task, and wait for your input |
| `/task-log-decision` | Persist a key design decision to the active task so it survives context compression        |

### Git workflow

| Skill     | What it does                                                                        |
| --------- | ----------------------------------------------------------------------------------- |
| `/gc`     | Commit — runs pre-commit tests, embeds the active `task:<id>` in the commit message |
| `/deploy` | Ship dev→test→main — runs unit gate, full integration suite, then merges to main    |

---

## Docs

- [Architecture](docs/ARCHITECTURE.md) — how the pipeline is structured and why
- [Task Framework](docs/arch/task_framework.md) — task lifecycle, the grooming/implementation/introspection skill trio, Execution Contract
- [Gates](docs/arch/gates.md) — internal + external gate mechanism, worked examples, how to add one
- [Setup](docs/setup.md) — installation and configuration
- [Skills](docs/skills.md) — full skill reference
