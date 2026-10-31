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

## Optional: open a task in a kitty tab and get a report back

If you run [kitty](https://sw.kovidgoyal.net/kitty/) with remote control on, `tasks__open_in_kitty` starts a task in its own new tab — a separate `claude` session working that task — so you can fan work out without leaving your current session. Nothing else in the framework depends on it; without kitty the tool just returns `not_opened` with a reason.

**Requirements:** kitty with remote control enabled (`allow_remote_control` / a `listen_on` socket), and `claude` on `PATH`. The socket is taken from `$KITTY_LISTEN_ON`, else discovered under `/tmp/kitty-*`; several live kittys and no way to choose is `not_opened`, never a guess.

| Argument | What it does |
|---|---|
| `task_id` | Required, so a bare call can never launch a tab. |
| `worktree` | The tab's working directory. Omitted, it is the task scope's local root; if that is unknown the tool does not guess. |
| `isolate` | `True` runs the tab in its own git worktree (`<repo>-worktrees/<task_id>`, branch `task/<task_id>`); `False` never does; omitted, it isolates only when another live tab already runs in that repo. The result's `worktree` / `isolated` say what was chosen. |
| `prompt` | Claude's initial argument in the tab. Omitted, it is a one-line pointer to read the task via `tasks__get` and run `/task-grooming` — never the brief itself (context is pulled, not pushed). An explicit prompt is never modified. |
| `model`, `model_reason` | **You choose the tab's model**: you have read the task, the launcher's rules have not. Pass `model` (`claude-sonnet-5`, `-sonnet-5-5`, `-opus-5`, `-opus-5-5`, cheapest to most capable) with `model_reason`, one line on why that tier fits; the reason is recorded verbatim on the task as its model decision, for introspection to grade. Precedence: your model, then the launcher's keyword/size rules, then claude's own default. Omitted, the tab still opens on the rules' pick and the result's `model_note` says it was a fallback. Never switch a tab's model with `/model`: it rewrites your global default in `~/.claude/settings.json`. |
| `parent` | Your session name, as `ListAgents` prints it (the server cannot know it). Given, the default prompt also tells the tab to `SendMessage` you once it has finished introspection or stops, with the task id and `done` or `abandoned`. `done` comes only after the tab recorded its one-line outcome as the reason on `tasks__finish` and linked its commits; `abandoned` means it stopped with the task still open. Either way the summary lives in the task and you pull it with `tasks__get`. |

**Getting the report back:** pass `parent` and the new tab messages your session when done; the message is a pointer (the task id plus `done` or `abandoned`), and the outcome and commits are in the task for you to pull, not pushed context. If `SendMessage` fails the tab says so in its own output and stops. Without `parent` the tab simply works the task and you check on it yourself.

**Result:** `{status, window_id, socket, reason, worktree, isolated, model, model_source}` (plus `model_note` when the tab opened without a caller-chosen model), with `status` one of `opened`, `exists` (a live tab already carries the task; nothing launched) or `not_opened`. Failures — no kitty, no `claude`, a timeout, a failed worktree — are always `not_opened` with a `reason`, never an exception. A task that has had a tab before and has none live is also `not_opened`: it points you at `resume`, which `open` never does for you.

**How it talks to kitty.** Everything goes through [`kitten @`](https://sw.kovidgoyal.net/kitty/remote-control/) against the remote-control socket, all in `taskfw/kitty/adapter.py` (pinned to kitty 0.49.2; a different major.minor is `not_opened`, and every call is time-bounded):

- *Launch:* `kitten @ --to <socket> launch --type=tab --cwd=<dir> --env … --var task_id=<id> --watcher=<watcher.py> -- claude --settings <hooks.json> -- "<prompt>"`. Argv is a list, never a shell string, so the prompt needs no escaping and is submitted at startup (no send-text race).
- *Identity:* the tab carries `TASKFW_TASK_ID`, `TASKFW_WORKTREE`, `TASKFW_DB` in its env, and a `task_id` [user var](https://sw.kovidgoyal.net/kitty/launch/) on its window. `exists` is detected by `kitten @ ls --match var:task_id=<id>`.
- *State:* Claude Code hooks (`taskfw/kitty/hooks.json`, injected with `--settings`, merged with your own) run `python -m taskfw.kitty.hook`, which sets `state` (`running` / `waiting` / `done`) and `last_active` user vars via `kitten @ set-user-vars` and backfills the Claude session id into the store. It acts only when `TASKFW_TASK_ID` is set and never fails the session.
- *Lifecycle:* kitty loads `watcher.py` itself and it appends window events to the store through a short-lived writer process (kitty's bundled Python has no `sqlite3`). Errors land in the log above, not in kitty.
- *Report back:* this is not a kitty channel at all — it is the tab's own Claude `SendMessage` to `parent`. Kitty only starts the tab and shows its state.

**MCP servers in a task tab:** a task tab (`start` / `resume` / `open`, not `plain`) launches with `--strict-mcp-config` and only `taskfw` + `claude-hooks`, copied at launch from the user-level `mcpServers` in `~/.claude.json` into a 0600 file under `<db dir>/mcp/` (never argv, since server env and headers would show in `ps`). Strict means project `.mcp.json` servers, `projects[...].mcpServers`, plugin servers and claude.ai connectors are absent from the tab by design. `TASKFW_KITTY_MCP` changes the set: a comma list of user-level server names, or empty / `all` for every server with no slimming. If `~/.claude.json` is unreadable or a named server is missing, the tab gets every server rather than a partial set. Each launch logs why it got the set it did: the `kitty launch` line under logger `taskfw.kitty` (stderr, and `tasks__logs(logger="taskfw.kitty")`) carries `mcp=<names>|all` and `mcp_source=<default|override|all-requested|fallback:<reason>>`, with `;drops=<…>` naming any project or plugin server strict mode hid.

**Not a task:** `python -m taskfw.kitty.launcher plain --cwd <dir> --parent <session> [--prompt P] [--title T] [--model M --model-reason R]` opens a claude tab that belongs to no task (e.g. to run a project skill) and prints its window id. `--parent` is required: it is the caller's session name as `ListAgents` prints it, and the prompt ends with a sentence telling the tab to SendMessage it the outcome when finished, since a plain tab has no task row to read a result from. It carries only `PATH`: no task identity, project hooks or store row. `--cwd` must be the directory holding the `.claude/skills` you want, since project skills load only from there. Choose its model the same way (`--model` with `--model-reason`); omitted, the prompt's keywords and length pick one and stderr says so.

**Throwaway tab from a session:** `tasks__open_plain_tab(parent, prompt, cwd?, title?, model?, model_reason?, max_life_minutes=60)` is the `plain` tab above reachable as an MCP tool, for a run you want to watch and discard (an experiment, a read-only check). No task row is created, so there is nothing to purge and list, search and grooming never see it; the active pointer is untouched. `parent` and `prompt` are required (`parent` is the caller's session name and the tab SendMessages it the outcome). `max_life_minutes` (1-60, default 60; over 60 is `not_opened`) ends the tab: claude runs under `perl -e 'alarm shift; exec @ARGV'`, so the limit lives in the window's own process and cannot close another window; no `perl` on `PATH` means no tab. Result: `{status, window_id, socket, reason, model, model_source, closes_in_minutes}` (plus `model_note` when no model was chosen). Use a real task instead when the work edits files or must leave a trail. The launcher form is `python -m taskfw.kitty.launcher open-plain --parent <session> [--cwd D] [--max-life-minutes N]` (JSON, always exit 0), and `plain` takes `--max-life-minutes` too.

**Related:** `TASKFW_KITTY_OPEN_CMD` replaces the launcher command (shell-split); `python -m taskfw.kitty.launcher cleanup <task_id>` removes a task's worktree and branch, refusing dirty or unmerged work. Tab lifecycle events are recorded in `~/.kitty-taskfw/store.sqlite` (override with `KITTY_TASKFW_STORE`); watcher errors go to `~/.kitty-taskfw/watcher-errors.log`.

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
