#!/usr/bin/env bash
# Two-phase deploy:
#   deploy.sh          → dev → test  (run tests, restart server from test worktree)
#   deploy.sh --ship   → test → main (final merge to main, no tests)
#
# Server always runs from ~/workspace/claude-hooks-test (test branch).
# Never touch main directly — only --ship merges into it.

set -euo pipefail

MAIN_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DEV_DIR="$(dirname "$MAIN_DIR")/claude-hooks-dev"
TEST_DIR="$(dirname "$MAIN_DIR")/claude-hooks-test"

SHIP=false
if [[ "${1:-}" == "--ship" ]]; then
    SHIP=true
fi

echo "=== claude-hooks deploy ==="

if $SHIP; then
    # --- Phase 2: test → main (ship) ---
    # --no-ff forces an explicit merge commit even when a fast-forward is
    # possible (main/test rarely diverge otherwise) — without it, main's log
    # is indistinguishable from a direct commit and the "this batch cleared
    # the test gate" checkpoint disappears from history entirely.
    echo "Merging test → main..."
    cd "$MAIN_DIR"
    git merge test --no-ff --no-edit -m "Merge branch 'test' into main (deploy.sh --ship)"
    echo "=== Shipped to main. ==="
    exit 0
fi

# --- Phase 1: dev → test ---

# 1. Confirm worktrees exist
for DIR in "$DEV_DIR" "$TEST_DIR"; do
    if [ ! -d "$DIR/.git" ] && [ ! -f "$DIR/.git" ]; then
        echo "ERROR: worktree not found at $DIR" >&2
        exit 1
    fi
done

# 2. Quick unit gate in dev (no server needed)
echo "Running unit tests in dev worktree..."
cd "$DEV_DIR"
uv run python -m pytest tests/ -q -m "not integration"
echo "Unit tests passed."

# 3. Merge dev → test
echo "Merging dev → test..."
cd "$TEST_DIR"
git merge dev --no-edit

# 4. Verify server is up, then restart via launchctl so it picks up the new code
PLIST_LABEL="com.debaditya.claude-hooks-pipeline"

PRE_HEALTH=$(curl -sf --max-time 3 http://127.0.0.1:8766/health || echo '{"status":"unreachable"}')
echo "Health (pre-restart): $PRE_HEALTH"
PRE_STATUS=$(echo "$PRE_HEALTH" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','?'))" 2>/dev/null || echo "?")
if [ "$PRE_STATUS" != "ok" ]; then
    echo "WARNING: Server was not running before restart — check launchd or start manually." >&2
fi

echo "Restarting hook server via launchctl..."
# Graceful SIGTERM, not `kickstart -k` (task:ac5df3db — kickstart -k's kill
# path was empirically leaving ~/.claude/langgraph_checkpoints.db in a state
# that threw "attempt to write a readonly database" on every subsequent
# request, breaking hook memory/context injection for the whole session
# until the next restart. hooks/server.py's lifespan() shutdown path exits
# the `with SqliteSaver.from_conn_string(...)` block to close the
# checkpoint DB connection cleanly — a graceful SIGTERM reliably gives it
# the chance to run that path before the process exits; kickstart -k did
# not, even though both logged a "shutting down" line (a race between the
# log call and the actual OS-level file-handle release under -k's faster
# kill path). KeepAlive=true in the plist auto-respawns the process the
# moment it exits, so no separate `launchctl start` is needed after this.
launchctl kill SIGTERM "gui/$(id -u)/$PLIST_LABEL"

# Poll instead of a fixed sleep — startup time varies (e.g. checkpoint DB
# compaction at lifespan() startup takes a few seconds and scales with DB
# size), and a fixed sleep here has repeatedly been too short in practice.
STATUS="unreachable"
for _ in $(seq 1 15); do
    sleep 1
    HEALTH=$(curl -sf --max-time 3 http://127.0.0.1:8766/health || echo '{"status":"unreachable"}')
    STATUS=$(echo "$HEALTH" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','?'))" 2>/dev/null || echo "?")
    if [ "$STATUS" = "ok" ]; then
        break
    fi
done
echo "Health (post-restart): $HEALTH"

if [ "$STATUS" != "ok" ]; then
    echo "WARNING: Server health check returned: $HEALTH" >&2
    exit 1
fi

# 5. Full suite (unit + integration) from test worktree against live server
# NOTE: pyproject.toml's addopts bakes in `-m "not integration"`, which silently
# wins over a bare `pytest tests/ -q` — must override the marker expression
# explicitly or integration tests never actually run despite this being the
# "full suite" step. (Discovered 2026-07-05: every prior /deploy run had been
# silently skipping all integration tests while reporting success.)
#
# Run unit and integration as two SEPARATE sequential invocations rather than
# one combined `-m "integration or not integration"` run — measured 2026-07-05:
# combined run took ~62s and intermittently failed a timing-sensitive perf
# test (-n auto oversubscribing across both suites at once); two sequential
# runs took ~40s total and didn't reproduce the flake.
echo "Running unit tests from test worktree..."
UNIT_OUTPUT=$(uv run python -m pytest tests/ -q -m "not integration" 2>&1)
echo "$UNIT_OUTPUT"
UNIT_COUNT=$(echo "$UNIT_OUTPUT" | tail -1 | grep -oE '^[0-9]+' || echo 0)

echo "Running integration tests from test worktree..."
INTEGRATION_OUTPUT=$(uv run python -m pytest tests/ -q -m "integration" 2>&1)
echo "$INTEGRATION_OUTPUT"
INTEGRATION_COUNT=$(echo "$INTEGRATION_OUTPUT" | tail -1 | grep -oE '^[0-9]+' || echo 0)

if [ "$INTEGRATION_COUNT" -eq 0 ]; then
    echo "ERROR: 0 integration tests ran — marker renamed/removed, or all integration tests deleted/deselected. Refusing to report a false-green full suite." >&2
    exit 1
fi
echo "Confirmed: unit=$UNIT_COUNT, integration=$INTEGRATION_COUNT tests ran."
echo "=== Deploy complete. Server is up on test. Run 'deploy.sh --ship' to merge to main. ==="
