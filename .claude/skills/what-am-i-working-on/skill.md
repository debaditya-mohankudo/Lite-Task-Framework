---
name: what-am-i-working-on
description: Show recent activity from server memory — prompts, tool calls, and task activations across sessions. Use when asked "what was I working on?" or invoked as /what-am-i-working-on.
user-invocable: true
updated: 2026-08-17
model: haiku
---

## Intent

Quick cold-start orientation tool. Fetches the last 50 events from the hook server's unified event log, plus which `claude` CLI processes are actually live right now, and presents both as a summary.

## How to use this skill

1. Call `mcp__claude-hooks__hooks__server_memory` with `n_events=50` — event log (prompts, tool calls, task activations). No transformation needed — the tool returns a formatted markdown table.

```python
mcp__claude-hooks__hooks__server_memory(n_events=50)
```

2. Fetch live session info from the hook server's `/session/live` endpoint (OS-level `ps`-based check for running `claude` CLI processes — catches stale/forgotten sessions that the event log alone won't show):

```bash
curl -s http://127.0.0.1:8766/session/live
```

Present it as: count of live sessions, and for each one its pid and elapsed time (`etime`).

If either call fails (event log tool returns `{error: ...}`, or the curl fails/connection refused), report that the hook server is unreachable and suggest checking `launchctl list | grep claude-hooks`.

3. Fetch the last 10 taskfw log events (everything in taskfw's own operational log — MCP tool calls like `tasks__check_item`, skill-invocation markers, server lifecycle lines — not just task-skill invocations, since that filter was hiding most of the log):

```python
mcp__taskfw__tasks__logs(limit=10)
```

No client-side filtering — present all 10 rows as-is: timestamp / logger / message. If the `mcp__taskfw__*` tools aren't available in this session, skip this section silently rather than failing the whole summary.
