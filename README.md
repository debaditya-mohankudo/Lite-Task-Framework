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
