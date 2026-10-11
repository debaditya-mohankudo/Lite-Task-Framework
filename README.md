# task-framework

Portable task tracking for AI coding agents. MCP-native, host-agnostic — any
MCP client reaches the whole framework through its tools, with nothing
host-specific required.

Everything is driven through skills. You don't call the tools directly; you
invoke a skill and it calls them for you.

## Install

```bash
git clone <this repo>
cd task-framework
uv sync
```

This installs two commands: `taskfw-mcp` (the MCP server) and
`taskfw-backfill` (a recovery CLI, see below).

## Connect it to your host

Add it as an MCP server. For Claude Code, in `.mcp.json` or your global MCP
config:

```json
{
  "mcpServers": {
    "taskfw": {
      "command": "uv",
      "args": ["run", "--project", "/path/to/task-framework", "taskfw-mcp"],
      "type": "stdio"
    }
  }
}
```

Any other MCP-speaking host works the same way — point it at `taskfw-mcp`.

## Configuration

All optional; sensible defaults out of the box.

| Variable | Default | What it controls |
|---|---|---|
| `TASKFW_DB` | `~/.taskfw/tasks.db` | Where the task database lives. Set this per-workspace if you want project-scoped task stores instead of one global store. |
| `TASKFW_LOG_LEVEL` | `INFO` | Logger verbosity. |
| `TASKFW_BUSY_TIMEOUT_MS` | `5000` | How long a writer waits on a lock before giving up, if you're running multiple sessions against the same DB. |

## Using it — skills, not tool calls

Copy or symlink `.claude/skills/` into whichever project you're using this
framework from — skills are discovered per-project, not from an installed
package — then work through the loop with slash commands:

| Skill | When to use it |
|---|---|
| `/task-create` | Start a new task or epic. |
| `/task-grooming` | Before implementing — pulls context, checks assumptions, records falsifiable risks. |
| `/task-implementation` | While working — how to stay on track and finish clean. |
| `/task-introspection` | After finishing — grades the risks grooming predicted, records what was learned. |
| `/task-framework` | General entry point if you're not sure which stage you're in. |
| `/task-skills-audit` | Sanity-checks the skills themselves stay consistent with each other. |
| `/commit` | Commit conventions for a repo using this framework — cites the task, links the commit, keeps the message honest. |

The shape of the loop: **create → groom → implement → introspect.** Grooming
makes predictions; introspection grades them. That feedback edge is the part
worth not skipping — skip it and the loop still runs, it just stops teaching
you anything.

## Recovering a missed commit link

Linking a commit to its task is a deliberate step (the `/commit` skill does
it), not something watched for automatically. If a commit lands without that
step — forgotten, or made outside the skill — nothing is lost:

```bash
taskfw-backfill --repo /path/to/some/repo
```

Re-derives every commit→task link straight from git history. Safe to run
anytime, and safe to run repeatedly — it never duplicates an existing link.

Add `--dry-run` to see what it would link without writing anything, or
`--since <rev>` to limit the scan.

## Multiple projects, one store or many

By default every project shares one task database (`~/.taskfw/tasks.db`) —
all tasks live in one flat pool, not separated by project. What *is*
workspace-scoped is the active-task pointer: each workspace path remembers
its own active task independently, so working in two projects at once
doesn't cross-activate each other's tasks.

If you want tasks themselves kept apart per project, point `TASKFW_DB` at a
different file per project — there's no built-in filtering by workspace, so
this is the way to get real separation.

## The Jira perspective

Jira gave software teams a system of record for the SDLC. Work had an owner and a state, commits pointed back at the ticket that caused them, and epics held the larger intent. Because all of it lived outside anyone's head, people could join, leave and hand off without losing why anything was done.

Two things make Jira a poor fit for a solo developer working with agents:

- **It was built for humans.** Its screens, boards, notifications and permissions assume a team of people clicking through a UI. An agent can't use that interface, and it can't resume from a board it never saw.
- **It's priced for the enterprise.** Seats, tiers and an admin to run it make sense for an organisation. They're overhead for one person.

task-framework takes the part of Jira that made the SDLC work, a durable and linked record of what was decided, built and learned, and makes it **agent-native and solo-sized**. It is one local SQLite file behind a local MCP server, with no hosted service, no seats and no admin. The developer and their agents share one record that survives every session, and every fact in it is something an agent can read, write and be held to.

| In Jira's SDLC | Here |
|---|---|
| Ticket, epic | `task`, with one `epic` boolean and one hierarchy rule |
| Workflow states | Four statuses: `open`, `blocked`, `done`, `abandoned` |
| Linked issues | Typed `task_edges` with a closed vocabulary |
| Commit linked to ticket | `task:<id>` leads the commit message, and `tasks__add_commit` records the link. `taskfw-backfill` rebuilds missed links from git history. |
| Acceptance criteria | Resolution checklist items, checked as they land |
| Comments, history | Decisions and logs recorded at the moment they happen |
| Grooming, sprint planning | `/task-grooming`: pull context, verify premises, record falsifiable risks |
| Retrospective | `/task-introspection`: grade grooming's predictions, then fold lessons into concepts and skills |
| Tribal knowledge | `concept_store`, `task_memory` and the ontology: architecture and lessons that outlive any one task |

Jira stops at the board. This framework adds the step Jira never closed, **grading the plan against what happened**. Grooming makes claims that can be proven wrong, and introspection checks them. Each task leaves behind something that makes the next one cheaper. Without that step, planning is only paperwork, and a solo developer has no team around to catch that.

Two further differences follow from the user being an agent:

- **Context is pulled, never pushed.** An agent gets only what it asks for (`tasks__context`, `task_memory__recall`). Anything left out for space is reported as left out, so a gap is never mistaken for an absence.
- **The rules live in one place.** Every client goes through the same store and the same checks, so no path can enforce different rules from another.
