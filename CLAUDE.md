# claude-hooks

## Concept Store

Architectural concepts for this repo are stored in `concept_store/concepts.json`.

**Seed (run once):**

```bash
uv run python scripts/extract_concepts.py
```

**Drift detection** runs automatically after every Edit/Write via a post-tool-use hook — prints `[concept-drift]` to stderr if a changed file's concepts diverge from the baseline. No output = no drift.

To re-seed after major refactors, delete `concept_store/concepts.json` and re-run the seed command.

**Manual reads/writes** (correcting a concept, adding a new one, checking what's stored for a module) should go through the `concept__get`/`concept__list`/`concept__upsert`/`concept__delete`/`concept__modules` MCP tools rather than hand-writing a Python script against `concept_store/store.py` — they take `repo` explicitly (required, no default), so they work the same way against this repo or any other non-Java target repo with a `concept_store/concepts.json`.

## Task Tracking

Use `/task-framework` to create, activate, and manage tasks for all multi-step work. Use `/task-create` when creating tasks that need the full body template with motivation, files, and design decisions.

Before starting work on subtasks, run `/task-grooming epic:<id>` (or `/task-grooming task:<id>`) — activates each task, audits the body for gaps, and reports readiness.

After closing a task, run `/task-introspection` — surfaces unlogged decisions, checks for stale memories, and encodes learnings.

Tasks persist across sessions, surface automatically when referenced, and build a development trail. Use TodoWrite only for ephemeral within-session sub-steps.

## Running Tests

The hook server runs from the **test worktree** (`~/workspace/claude-hooks-test`, port 8766). Dev worktree edits never affect the running server.

Run unit tests in dev at any time (fast, no server needed):

```bash
cd ~/workspace/claude-hooks-dev
uv run python -m pytest tests/ -q -m "not integration"
```

To deploy and run the full suite, use `/deploy`.

## Recent Activity / Conversation History

To see "what was I working on?" — use `/what-am-i-working-on`. It fetches recent prompts, MCP tool calls, and activated tasks as a single chronological timeline from the hook server's event log.

Returns `{error}` if the server is down — check `launchctl list | grep claude-hooks`.

## Development Workflow (git worktree)

The hook server runs from `~/workspace/claude-hooks-test` (test branch) with a
**SqliteSaver** checkpoint at `~/.claude/langgraph_checkpoints.db`. State persists to
disk. It runs on port **8766**. `/deploy` restarts it after each merge.

Develop in the isolated worktree at `~/workspace/claude-hooks-dev` (dev branch):

```bash
# 1. Edit in dev worktree
cd ~/workspace/claude-hooks-dev

# 2. Quick unit tests (no server needed)
uv run python -m pytest tests/ -q -m "not integration"

# 3. Commit
/gc

# 4. Deploy to test + full suite + ship to main
/deploy
```

**Key rules:**

- Edits go in `~/workspace/claude-hooks-dev` (dev branch) — never touch main or test directly
- `/gc` commits target `--repo ~/workspace/claude-hooks-dev`
- Server runs from `claude-hooks-test` — dev edits never disrupt live Claude Code hooks
- main is never touched except by `/deploy`

## Observability

All hook logs write to `claude_hooks.sqlite` in iCloud via `sqlite_log_handler.py`.

**Always use the MCP tool to read logs — never query the DB directly with sqlite3:**

```text
mcp__claude-hooks__hooks__read_logs_sqlite
```

## Memory Store
MEMORY.sqlite - Use memory__ mcp tools
