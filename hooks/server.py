"""FastAPI hook server — persistent process replacing per-invocation subprocess dispatcher.

Routes: POST /hook/{event} for UserPromptSubmit | PreToolUse | PostToolUse | Stop | SessionStart | SessionEnd
State:  MemorySaver (in-process, in-memory) — does NOT survive server restarts.
        task:b3964f85 — replaced SqliteSaver (~/.claude/langgraph_checkpoints.db) after
        two corruption incidents (a 1.7GB .old-bloated file, then a
        "database disk image is malformed" failure that silently broke every
        Stop-hook write). Per-thread checkpoint history is capped at 5000 rows,
        enforced from NoopNode on every Stop event (langchain_learning/nodes/noop.py)
        — MemorySaver has no built-in eviction, so without this a long-running
        session would grow RAM usage unboundedly instead of corrupting a file.
Launch: uvicorn hooks.server:app --host 127.0.0.1 --port 8766

Subprocess dispatcher (dispatcher.py) remains untouched for fallback / testing.
"""
from __future__ import annotations

import sys
from contextlib import asynccontextmanager

from hooks.paths import PROJECT_ROOT as _PROJECT_ROOT, HOOKS_DIR as _HOOKS_DIR
for _p in (str(_PROJECT_ROOT), str(_HOOKS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import time

from fastapi import FastAPI, Form, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.logger import get_logger, setup

log = get_logger(__name__)
_slog = setup("server")


@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio
    from langgraph.checkpoint.memory import MemorySaver
    import langchain_learning.session_graph as sg
    import os as _os_mod

    pid = _os_mod.getpid()
    loop_impl = type(asyncio.get_running_loop()).__module__
    log.info("hook-server[pid=%d]: lifespan startup begin, event_loop=%s", pid, loop_impl)
    checkpointer = MemorySaver()
    sg._graph = sg.build_session_graph(checkpointer=checkpointer)
    import hooks.server_memory as server_memory
    server_memory.load()
    log.info("hook-server[pid=%d]: started, graph built with MemorySaver, server_session=%s", pid, server_memory.SERVER_SESSION_ID)
    yield
    log.info("hook-server[pid=%d]: shutdown, in-memory checkpoint state discarded", pid)
    sg._graph = None


from hooks.ui.deps import render as _render, error_partial as _error_partial, JINJA_ENV as _JINJA_ENV
from hooks.ui.routes import ui_router

app = FastAPI(lifespan=lifespan)
app.mount("/ui/static", StaticFiles(directory=str(_HOOKS_DIR / "static")), name="ui-static")
app.include_router(ui_router)


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    if request.url.path.startswith("/ui"):
        return _error_partial(f"HTTP {exc.status_code}", str(exc.detail))
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    log.error("unhandled exception: %s", exc, exc_info=True)
    if request.url.path.startswith("/ui"):
        return _error_partial("Something went wrong", str(exc))
    return JSONResponse({"detail": "Internal server error"}, status_code=500)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log method, path, status, and elapsed ms for every request via the bare 'server' logger."""
    t0 = time.perf_counter()
    response = await call_next(request)
    elapsed = int((time.perf_counter() - t0) * 1000)
    _slog.info("HTTP %s %s → %d  %dms", request.method, request.url.path, response.status_code, elapsed)
    return response


@app.post("/hook/UserPromptSubmit")
async def user_prompt_submit(request: Request):
    """UserPromptSubmit hook — runs the full UPS LangGraph chain.

    Injects memories, tool hints, domain classification, and task context into the session
    checkpoint. Returns hookSpecificOutput.additionalSystemPrompt for Claude to consume.
    All lc.* node logs write immediately to claude_hooks.sqlite via SQLiteHandler.
    """
    from hooks.dispatcher import _handle_user_prompt_submit, _extract_prompt
    body = await request.json()
    result = _handle_user_prompt_submit(body)
    try:
        import hooks.server_memory as server_memory
        server_memory.record_prompt(body.get("session_id", ""), _extract_prompt(body))
    except Exception as exc:
        log.warning("server_memory: record_prompt failed: %s", exc)
    return JSONResponse(content=result or {})


@app.post("/hook/PreToolUse")
async def pre_tool_use(request: Request):
    """PreToolUse hook — runs gate_check node against the current session checkpoint.

    Returns permissionDecision=deny with a reason if a gated tool (e.g. imessage__send)
    is called without its prereq (e.g. contacts__search) in the session. Returns {} to
    allow. Gate internals (name_arg_check, ALLOW/DENY rows, prompt_id correlation)
    write immediately to claude_hooks.sqlite via SQLiteHandler.
    """
    from hooks.dispatcher import _handle_pre_tool_use
    body = await request.json()
    result = _handle_pre_tool_use(body)
    return JSONResponse(content=result or {})


@app.post("/hook/PostToolUse")
async def post_tool_use(request: Request):
    """PostToolUse hook — runs log_tool_usage node and conditional task-lifecycle bridge nodes.

    Upserts tool hint row in tool_hints.sqlite (skipped for test sessions).
    Bridge nodes fire when tool_name matches a lifecycle tool (tasks__set_active,
    tasks__pop_active, tasks__clear_active, tasks__finish, tasks__add_decision) —
    they write task activation state into the MemorySaver checkpoint so the next
    UPS turn sees the updated active task. Always returns {}.
    """
    from hooks.dispatcher import _handle_post_tool_use
    body = await request.json()
    result = _handle_post_tool_use(body)
    try:
        import hooks.server_memory as server_memory
        server_memory.record_tool_from_hook(body)
        server_memory.record_task_from_hook(body)
    except Exception as exc:
        log.warning("server_memory: record failed: %s", exc)
    return JSONResponse(content=result or {})


@app.post("/hook/Stop")
async def stop(request: Request):
    """Stop hook — finalises the *turn*, NOT the session.

    Fires at the end of every assistant response. Clears per-turn ephemeral fields
    (via run_stop) but must NOT evict the checkpoint — that would wipe cross-turn
    state (active task, turn counter) every turn. Session eviction happens on
    SessionEnd. Normally returns {}; returns a one-shot decision:"block" +
    sound-alert reason on the first Stop of a turn (see NoopNode).
    """
    from hooks.dispatcher import _handle_stop
    body = await request.json()
    result = _handle_stop(body)
    return JSONResponse(content=result or {})


@app.post("/hook/SessionStart")
async def session_start(request: Request):
    """SessionStart hook — logs each new or resumed session."""
    body = await request.json()
    from hooks.dispatcher import _handle_session_start
    _handle_session_start(body)
    return JSONResponse(content={})


@app.post("/hook/SessionEnd")
async def session_end(request: Request):
    """SessionEnd hook — the session has actually closed; evict its checkpoint.

    This is the correct place to reclaim MemorySaver storage (fires once when the
    session ends, unlike Stop which fires every turn). Always returns {}.
    """
    body = await request.json()
    from hooks.dispatcher import _handle_session_end
    _handle_session_end(body)
    return JSONResponse(content={})


@app.get("/health")
async def health():
    """Health check — returns status=ok."""
    return {"status": "ok"}


@app.get("/session/active")
async def session_active():
    """Active task — returns the task currently active in the live MemorySaver checkpoint.

    Returns {task_id, title, session_id, turn} if a task is active, or {} if none.
    Source is the in-memory MemorySaver (not the DB) so reflects real-time state.
    """
    from hooks.ui.deps import get_active_session
    return JSONResponse(content=get_active_session())


@app.get("/session/current")
async def session_current():
    """Current session_id — from the single most-recent checkpoint write, no active
    task required. Use this (not /session/active) when no task has been activated
    yet — that's the case /session/active can't answer, since it only returns a
    session_id when active_task_id is set. Returns {} if no checkpoint exists yet.
    """
    from hooks.ui.deps import get_current_session
    return JSONResponse(content=get_current_session())


@app.get("/session/memory")
async def session_memory(n_events: int = 50):
    """Server session memory — last N events from the unified chronological timeline.

    Free-flowing event log: prompts, tool calls, task activations, and assistant
    turns interleaved with timestamps. Durable across reloads (SQLite-backed).
    """
    import hooks.server_memory as server_memory
    return server_memory.get_server_memory(n_events=n_events)






@app.get("/session/{session_id}")
async def session_detail(session_id: str):
    """Full checkpoint state for one session — latest channel_values from the live MemorySaver.

    Returns {session_id, turn_count, state} where state is the checkpoint's channel_values
    dict (active_task_id, turn, domains, etc.) as of the most recent write for this thread.
    Returns {"detail": "not found"} (404) if no checkpoint exists for session_id.
    """
    import langchain_learning.session_graph as sg
    checkpointer = sg._graph.checkpointer if sg._graph else None
    if not checkpointer:
        return JSONResponse(content={"detail": "not found"}, status_code=404)

    turn_count = 0
    latest_state: dict = {}
    found = False
    for tup in checkpointer.list({"configurable": {"thread_id": session_id}}):
        found = True
        turn_count += 1
        latest_state = tup.checkpoint.get("channel_values", {})

    if not found:
        return JSONResponse(content={"detail": "not found"}, status_code=404)

    return JSONResponse(content={
        "session_id": session_id,
        "turn_count": turn_count,
        "state": latest_state,
    })


@app.get("/session")
async def session():
    """Session list — returns all sessions with checkpoint counts from the live checkpointer."""
    import langchain_learning.session_graph as sg
    checkpointer = sg._graph.checkpointer if sg._graph else None
    counts: dict[str, int] = {}
    if checkpointer:
        try:
            for tup in checkpointer.list(None):
                sid = tup.config["configurable"]["thread_id"]
                counts[sid] = counts.get(sid, 0) + 1
        except Exception:
            pass
    sessions = [{"session_id": sid, "turns": n} for sid, n in counts.items()]
    return {"count": len(sessions), "sessions": sessions}
