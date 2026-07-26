"""FastAPI APIRouter for all /ui/* routes.

Mounted into the main app via app.include_router(ui_router) in hooks/server.py.
All shared helpers live in hooks/ui/deps.py to avoid circular imports.
"""
from __future__ import annotations

from collections import OrderedDict

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from hooks.ui.deps import (
    render, error_partial,
    parse_body_fields, valid_status,
    get_active_session,
    render_doc, list_docs,
    mem_list, mem_get,
    _PROJECT_ROOT,
)
from src.logger import get_logger

_log = get_logger(__name__)

ui_router = APIRouter()


# ---------------------------------------------------------------------------
# Root redirect
# ---------------------------------------------------------------------------

@ui_router.get("/ui/", response_class=HTMLResponse)
async def ui_root(request: Request):
    """Redirect /ui/ to the tasks page."""
    return RedirectResponse(url="/ui/tasks/", status_code=302)


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------

@ui_router.get("/ui/tasks/", response_class=HTMLResponse)
async def ui_index(request: Request, status: str = "open", tag: str = ""):
    """Task Manager UI — three-column layout: tag cloud | task tree | task detail."""
    from src.tools.tasks import handle_list
    from collections import Counter

    status = valid_status(status)
    all_tasks = handle_list(status=status, format="json")

    # Tag cloud: count label tags (skip structural prefixes)
    _SKIP = {"parent:", "project:", "domain:", "type:", "epic", "story", "task", "bug", "subtask"}
    tag_counter: Counter = Counter()
    for t in all_tasks:
        for raw in (t.get("tags") or "").split(","):
            raw = raw.strip()
            if not raw:
                continue
            if any(raw.startswith(p) for p in _SKIP) or raw in _SKIP:
                continue
            tag_counter[raw] += 1

    tag_cloud = tag_counter.most_common(30)  # list of (tag, count)

    # Filter by tag if requested
    if tag:
        tasks = [t for t in all_tasks if tag in (t.get("tags") or "").split(",")]
    else:
        tasks = all_tasks

    return render("ui/tasks/list.html", tasks=tasks, status=status,
                  tag_cloud=tag_cloud, active_tag=tag)


@ui_router.get("/ui/tasks/body-fields", response_class=HTMLResponse)
async def ui_body_fields(issue_type: str = "task"):
    """Dynamic body fields partial — swapped by HTMX on issue_type change in create form."""
    return render("ui/partials/task_body_fields.html", issue_type=issue_type)


@ui_router.get("/ui/tasks/new", response_class=HTMLResponse)
async def ui_task_new(request: Request):
    """Create task form partial — loaded into the detail panel."""
    from src.tools.tasks import handle_list
    epics = [t for t in handle_list(status="open", format="json") if t.get("issue_type") in ("epic", "story")]
    return render("ui/partials/create_form.html", epics=epics, error="", issue_type="task")


@ui_router.get("/ui/tasks/{task_id}", response_class=HTMLResponse)
async def ui_task_detail(task_id: str):
    """Task detail partial — swapped into #right-panel by HTMX on task row click."""
    from src.tools.tasks import handle_get, handle_history, handle_neighbors
    import langchain_learning.session_graph as sg

    task = handle_get(task_id)
    if "error" in task:
        return HTMLResponse(f"<div class='empty-state'>Task not found: {task_id}</div>")

    history = handle_history(task_id)
    turns = [
        e for e in history
        if e.get("tools") != "decision" and not e.get("session_id", "").startswith("replay-")
    ]
    decisions = [e for e in history if e.get("tools") == "decision"]

    # Group turns by session for cross-session collapse
    _session_groups: OrderedDict = OrderedDict()
    for ev in turns:
        sid = ev.get("session_id") or "unknown"
        _session_groups.setdefault(sid, []).append(ev)
    turn_sessions = [
        {"session_id": sid, "events": evts, "is_current": False}
        for sid, evts in _session_groups.items()
    ]
    if turn_sessions:
        turn_sessions[-1]["is_current"] = True
        if turn_sessions[-1]["events"]:
            turn_sessions[-1]["events"][-1]["is_latest"] = True

    try:
        neighbors = handle_neighbors(task_id)
    except Exception:
        neighbors = []

    parent = None
    if task.get("parent_id"):
        p = handle_get(task["parent_id"])
        if "error" not in p:
            parent = p

    # live session check — skip for closed tasks (DB status is authoritative)
    live_session = None
    live_turn = 0
    if task.get("status") not in ("done", "abandoned"):
        try:
            checkpointer = getattr(sg._graph, "checkpointer", None)
            if checkpointer:
                for tup in checkpointer.list(None):
                    state = tup.checkpoint.get("channel_values", {})
                    if state.get("active_task_id") == task_id:
                        live_session = tup.config["configurable"]["thread_id"]
                        live_turn = state.get("turn", 0)
                        break
        except Exception as exc:
            _log.warning("live session check failed for task=%s: %s", task_id, exc)

    _STRUCTURED_PREFIXES = ("parent:", "project:", "domain:", "frozen")
    all_tags = [t.strip() for t in (task.get("tags") or "").split(",") if t.strip()]
    structured_tags = [t for t in all_tags if any(t.startswith(p) or t == p for p in _STRUCTURED_PREFIXES)]
    label_tags = [t for t in all_tags if t not in structured_tags]

    body_fields = parse_body_fields(task.get("body") or "")

    return render(
        "ui/partials/task_detail.html",
        task=task, turns=turns, decisions=decisions,
        turn_sessions=turn_sessions,
        neighbors=neighbors, parent=parent,
        live_session=live_session, live_turn=live_turn,
        structured_tags=structured_tags, label_tags=label_tags,
        body_fields=body_fields,
    )


@ui_router.post("/ui/tasks", response_class=HTMLResponse)
async def ui_task_create(
    title: str = Form(...),
    body_task: str = Form(""),
    body_motivation: str = Form(""),
    body_resolution: str = Form(""),
    issue_type: str = Form("task"),
    parent_id: str = Form(""),
):
    """Create a task via the web form. Returns refreshed sidebar on success."""
    from src.tools.tasks import handle_create, handle_list
    from hooks.gates import validate_jira_hierarchy

    parts = ["Type: feature"]
    if body_task:       parts.append(f"\nTask: {body_task.strip()}")
    if body_motivation: parts.append(f"\nMotivation: {body_motivation.strip()}")
    if body_resolution: parts.append(f"\nResolution: {body_resolution.strip()}")
    body = "\n".join(parts)

    error = validate_jira_hierarchy(issue_type, parent_id)
    result: dict = {}
    if not error:
        result = handle_create(
            title=title, body=body, issue_type=issue_type,
            parent_id=parent_id, cwd=str(_PROJECT_ROOT),
        )
        error = result.get("error")
    if error:
        epics = [t for t in handle_list(status="open", format="json") if t.get("issue_type") in ("epic", "story")]
        return render("ui/partials/create_form.html", epics=epics, error=error)
    tasks = handle_list(status="open", format="json")
    return render("ui/partials/sidebar.html", tasks=tasks, status="open")


# ---------------------------------------------------------------------------
# Sidebar / cockpit / search
# ---------------------------------------------------------------------------

@ui_router.get("/ui/sidebar", response_class=HTMLResponse)
async def ui_sidebar(request: Request, status: str = "open"):
    """Sidebar partial — returned by HTMX status-tab clicks."""
    from src.tools.tasks import handle_list
    status = valid_status(status)
    tasks = handle_list(status=status, format="json")
    active = get_active_session()
    return render("ui/partials/sidebar.html", tasks=tasks, status=status,
                  active_task_id=active.get("task_id", ""))


@ui_router.get("/ui/cockpit", response_class=HTMLResponse)
async def ui_cockpit():
    """Cockpit strip partial — polled every 10s by base.html to show active task."""
    active = get_active_session()
    return render("ui/partials/cockpit.html", active=active)


@ui_router.get("/ui/search", response_class=HTMLResponse)
async def ui_search(q: str = ""):
    """Search partial — tasks + decisions + memories for the search overlay."""
    import sqlite3 as _sqlite3
    import os as _os
    from src.tools.tasks import handle_search, _connect

    q = q.strip()
    if len(q) < 2:
        return HTMLResponse("")

    raw = handle_search(q, status="open,done,abandoned")[:12]
    for t in raw:
        tags = (t.get("tags") or "").split(",")
        t["project"] = next((tag.replace("project:", "") for tag in tags if tag.startswith("project:")), "")
    tasks = raw

    with _connect() as conn:
        decisions = [dict(d) for d in conn.execute(
            """SELECT e.summary, e.turn, e.logged_at, e.task_id, t.title as task_title
               FROM task_events e
               LEFT JOIN open_tasks t ON t.id = e.task_id
               WHERE e.tools = 'decision' AND lower(e.summary) LIKE lower(?)
               ORDER BY e.logged_at DESC LIMIT 6""",
            (f"%{q}%",),
        ).fetchall()]

    mem_db = _os.path.expanduser("~/.claude/MEMORY.sqlite")
    memories = []
    if _os.path.exists(mem_db):
        with _sqlite3.connect(mem_db) as mconn:
            mconn.row_factory = _sqlite3.Row
            memories = [dict(r) for r in mconn.execute(
                """SELECT name, type, domain, body
                   FROM memories
                   WHERE lower(body) LIKE lower(?) OR lower(name) LIKE lower(?)
                   ORDER BY updated DESC LIMIT 3""",
                (f"%{q}%", f"%{q}%"),
            ).fetchall()]

    return render("ui/partials/search_results.html", q=q, tasks=tasks,
                  decisions=decisions, memories=memories)


# ---------------------------------------------------------------------------
# Memory
# ---------------------------------------------------------------------------

@ui_router.get("/ui/memory/", response_class=HTMLResponse)
async def ui_memory_list(domain: str = "", type: str = "", selected: str = ""):
    """Memory browser — MEMORY.sqlite with optional domain/type filter."""
    memories, domains, types = mem_list(domain=domain, type_filter=type)
    return render("ui/memory/list.html",
                  memories=memories, domains=domains, types=types,
                  active_domain=domain, active_type=type, selected=selected)


@ui_router.get("/ui/memory/{slug}", response_class=HTMLResponse)
async def ui_memory_detail(slug: str):
    """Memory detail partial — swapped into #right-panel by HTMX on row click."""
    memory = mem_get(slug)
    if memory is None:
        return HTMLResponse(f"<div class='empty-state'>Memory not found: {slug}</div>")
    return render("ui/memory/detail.html", memory=memory)


# ---------------------------------------------------------------------------
# Docs
# ---------------------------------------------------------------------------

@ui_router.get("/ui/docs/", response_class=HTMLResponse)
async def ui_docs_list(request: Request, doc: str = ""):
    """Docs browser — lists docs/*.md files, renders selected doc to HTML."""
    docs = list_docs()
    selected_title, selected_html = "", ""
    if doc:
        result = render_doc(doc)
        if result:
            selected_title, selected_html = result
    elif docs:
        result = render_doc(docs[0]["slug"])
        if result:
            doc = docs[0]["slug"]
            selected_title, selected_html = result
    return render("ui/docs/list.html", docs=docs, active_doc=doc,
                  selected_title=selected_title, selected_html=selected_html)


@ui_router.get("/ui/docs/{slug:path}", response_class=HTMLResponse)
async def ui_docs_detail(slug: str):
    """Doc detail partial — swapped into #right-panel by HTMX on doc click."""
    result = render_doc(slug)
    if not result:
        return HTMLResponse(f"<div class='empty-state'>Doc not found: {slug}</div>")
    title, html = result
    return render("ui/docs/detail.html", title=title, html=html, slug=slug)
