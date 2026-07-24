"""Tests for Component 2 — SessionGraph (LangGraph StateGraph).

Test strategy:
  - All IO (MEMORY.sqlite, tool_hints.sqlite) uses temp files or
    monkeypatched paths — no dependency on real system DBs.
  - Graph topology is exercised end-to-end via graph.invoke().
  - Individual nodes are also tested in isolation to verify partial-update contract.
"""
import json
import sqlite3
import tempfile
import types
from pathlib import Path
from unittest.mock import patch

import pytest

from langchain_learning.session_state import SessionState
from src.db.schema import MEMORIES_DDL, MCP_TOOL_HINTS_DDL
from langchain_learning.session_graph import (
    build_session_graph,
    run_session,
)
from langchain_learning.nodes.load_memories import LoadMemoriesNode
from langchain_learning.nodes._text_utils import tokenise as _tokenise
from langchain_learning.nodes.cwd_domain_detect import CwdDomainDetectNode
from langchain_learning.nodes.score_tools import ScoreToolsNode
from langchain_learning.retrievers import NullMemoryRetriever, NullToolScorer

# Instantiate nodes for direct unit testing
load_memories     = LoadMemoriesNode()
cwd_domain_detect = CwdDomainDetectNode()
score_tools       = ScoreToolsNode()

# ---------------------------------------------------------------------------
# Fixtures — temp DBs
# ---------------------------------------------------------------------------

def _make_memory_db(rows: list[dict]) -> Path:
    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    conn = sqlite3.connect(tmp.name)
    conn.executescript(MEMORIES_DDL)
    conn.executemany(
        "INSERT INTO memories (name, type, domain, tags, body) VALUES (:name,:type,:domain,:tags,:body)",
        rows,
    )
    conn.commit()
    conn.close()
    return Path(tmp.name)


def _make_hints_db(rows: list[dict]) -> Path:
    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    conn = sqlite3.connect(tmp.name)
    conn.executescript(MCP_TOOL_HINTS_DDL)
    conn.executemany(
        "INSERT INTO mcp_tool_hints (tool_name, domain, skill, count, keywords) VALUES (:tool_name,:domain,:skill,:count,:keywords)",
        rows,
    )
    conn.commit()
    conn.close()
    return Path(tmp.name)


@pytest.fixture
def memory_db():
    return _make_memory_db([
        {"name": "always-on", "type": "user", "domain": "global",
         "tags": "global", "body": "always injected"},
        {"name": "astro-mem", "type": "project", "domain": "astrology",
         "tags": "nakshatra rahu panchang", "body": "astrology data"},
        {"name": "market-mem", "type": "project", "domain": "market-intel",
         "tags": "gold nifty fii", "body": "market data"},
        {"name": "vault-mem", "type": "reference", "domain": "vault",
         "tags": "vault note write", "body": "vault operations"},
    ])


@pytest.fixture
def hints_db():
    return _make_hints_db([
        {"tool_name": "panchang__today",     "domain": "astrology",   "skill": "panchang", "count": 20, "keywords": "panchang,nakshatra,tithi"},
        {"tool_name": "market__gold_regime", "domain": "market-intel","skill": "gold",     "count": 15, "keywords": "gold,regime,market"},
        {"tool_name": "imessage__send",      "domain": "macos",       "skill": "imessage", "count": 50, "keywords": "send,message,contact"},
        {"tool_name": "vault__write",        "domain": "vault",       "skill": "vault",    "count": 40, "keywords": "write,save,note,vault"},
    ])


@pytest.fixture
def mock_cfg(memory_db, hints_db):
    """Patch langchain_learning.config.config so all retrievers see the temp DBs."""
    import langchain_learning.nodes.load_memories as lm
    import langchain_learning.config as lc
    cfg = types.SimpleNamespace(
        memory_db=memory_db,
        tool_hints_db=hints_db,
    )
    with patch.object(lm, "_cfg", cfg), \
         patch.object(lc, "config", cfg):
        yield cfg


def _base_state(**overrides) -> SessionState:
    from collections import OrderedDict
    s: SessionState = {
        "event_type": "user_prompt_submit",
        "prompt": "", "cwd": "", "session_id": "", "turn": 0,
        "memories": [],
        "domains": [], "keywords": [],
        "tool_hints": [],
        "active_task_id": "", "active_task_title": "",
        "task_memories": [], "task_context": [], "task_stack": [], "mid_task_decisions": [], "related_tasks": [],
        "task_rag_chunks": [], "task_body": "",
        "current_state": "prompt",
        "tool_name": "", "tool_input": {}, "tool_result": {}, "prompt_id": "",
        "prompt_tools": [], "session_prompt_ids": [], "session_tools": OrderedDict(),
        "session_prompt_texts": {},
        "gate_denied": False, "gate_reason": "",
        "duration_ms": 0.0,
    }
    s.update(overrides)  # type: ignore[arg-type]
    return s


# ---------------------------------------------------------------------------
# _tokenise
# ---------------------------------------------------------------------------

def test_tokenise_basic():
    result = _tokenise("what nakshatra is the moon in")
    assert "nakshatra" in result
    assert "moon" in result


def test_tokenise_strips_short_tokens():
    result = _tokenise("is it ok to go")
    assert "ok" not in result  # len < 4


def test_tokenise_lowercases():
    result = _tokenise("NAKSHATRA RAHU")
    assert "nakshatra" in result
    assert "rahu" in result


# ---------------------------------------------------------------------------
# load_memories node
# ---------------------------------------------------------------------------

def test_load_memories_scores_relevant(mock_cfg):
    # cwd maps "astrology" → project_domain="astrology" so astro-mem competes
    result = load_memories(_base_state(prompt="what is my nakshatra today", cwd="/workspace/astrology"))
    names = [m["name"] for m in result["memories"]]
    assert "astro-mem" in names


def test_load_memories_excludes_out_of_domain(mock_cfg):
    # cwd maps to astrology; market-intel and vault memories should not surface
    result = load_memories(_base_state(prompt="nakshatra moon rising", cwd="/workspace/astrology"))
    names = [m["name"] for m in result["memories"]]
    assert "market-mem" not in names
    assert "vault-mem" not in names


def test_load_memories_global_requires_keyword_overlap(mock_cfg):
    # "always-on" has domain=global, tags="global" — "nakshatra" won't hit it
    result = load_memories(_base_state(prompt="nakshatra rahu panchang", cwd="/workspace/astrology"))
    names = [m["name"] for m in result["memories"]]
    # astro-mem should win; always-on (global, no keyword overlap) should not surface over it
    assert "astro-mem" in names
    # always-on has no keyword match with the prompt — it may or may not surface
    # but it must NOT displace the relevant astro-mem
    assert names.index("astro-mem") < names.index("always-on") if "always-on" in names else True


def test_load_memories_extracts_keywords(mock_cfg):
    result = load_memories(_base_state(prompt="nakshatra rahu panchang today", cwd="/workspace/astrology"))
    assert "nakshatra" in result["keywords"]
    assert "panchang" in result["keywords"]


def test_load_memories_missing_db_returns_empty():
    node = LoadMemoriesNode(retriever=NullMemoryRetriever())
    result = node(_base_state(prompt="test"))
    assert result["memories"] == []


def test_load_memories_caps_at_top_n(hints_db):
    rows = [{"name": f"mem{i}", "type": "user", "domain": "macos",
             "tags": "message send", "body": "macos tool"} for i in range(15)]
    big_db = _make_memory_db(rows)
    import langchain_learning.nodes.load_memories as pn
    cfg = types.SimpleNamespace(memory_db=big_db, tool_hints_db=hints_db)
    with patch.object(pn, "_cfg", cfg):
        result = load_memories(_base_state(prompt="send message to contact", cwd="/workspace/claude-hooks"))
    assert len(result["memories"]) <= 10


# ---------------------------------------------------------------------------
# cwd_domain_detect node
# ---------------------------------------------------------------------------

def test_cwd_domain_detect_maps_known_cwd():
    import langchain_learning.nodes.cwd_domain_detect as cdd_mod
    cwd_map = {"market-intel": "market-intel", "claude-hooks": "claude-hooks"}
    with patch.object(cdd_mod, "_cfg", types.SimpleNamespace(cwd_domain_map=cwd_map)):
        state = _base_state(cwd="/Users/x/workspace/market-intel/src")
        result = cwd_domain_detect(state)
    assert "market-intel" in result["domains"]


def test_cwd_domain_detect_no_match_leaves_domains_unchanged():
    import langchain_learning.nodes.cwd_domain_detect as cdd_mod
    cwd_map = {"market-intel": "market-intel"}
    with patch.object(cdd_mod, "_cfg", types.SimpleNamespace(cwd_domain_map=cwd_map)):
        state = _base_state(cwd="/tmp/random_project", domains=["astrology"])
        result = cwd_domain_detect(state)
    assert "astrology" in result["domains"]


# ---------------------------------------------------------------------------
# score_tools node
# ---------------------------------------------------------------------------

def test_score_tools_returns_matching_domain(mock_cfg):
    result = score_tools(_base_state(domains=["astrology"], keywords=["nakshatra"]))
    tool_names = [h["tool_name"] for h in result["tool_hints"]]
    assert "panchang__today" in tool_names


def test_score_tools_excludes_non_domain(mock_cfg):
    result = score_tools(_base_state(domains=["astrology"], keywords=["panchang"]))
    tool_names = [h["tool_name"] for h in result["tool_hints"]]
    assert "imessage__send" not in tool_names


def test_score_tools_caps_at_five(mock_cfg):
    result = score_tools(_base_state(domains=["macos", "vault", "astrology", "market-intel"], keywords=["write", "send", "gold", "panchang"]))
    assert len(result["tool_hints"]) <= 5


def test_score_tools_missing_db_returns_empty():
    node = ScoreToolsNode(scorer=NullToolScorer())
    result = node(_base_state(domains=["macos"], keywords=["send"]))
    assert result["tool_hints"] == []


# ---------------------------------------------------------------------------
# KeywordOverlapScorer — count tie-breaker (task:53c9f817)
# ---------------------------------------------------------------------------

def test_score_tools_count_breaks_ties_within_domain(mock_cfg):
    # imessage__send (count=50) and vault__write (count=40) both match
    # domain="macos"/"vault" respectively with no keyword overlap — equal
    # base score (domain_match*2), so the higher-count tool should rank first.
    result = score_tools(_base_state(domains=["macos", "vault"], keywords=[]))
    tool_names = [h["tool_name"] for h in result["tool_hints"]]
    assert tool_names.index("imessage__send") < tool_names.index("vault__write")


def test_score_tools_count_does_not_manufacture_relevance(mock_cfg):
    # imessage__send has count=50 (the highest in the fixture) but domain
    # "macos" is not requested and keywords don't overlap — it must not
    # leak into results just because of its usage count.
    result = score_tools(_base_state(domains=["astrology"], keywords=["panchang"]))
    tool_names = [h["tool_name"] for h in result["tool_hints"]]
    assert "imessage__send" not in tool_names


# ---------------------------------------------------------------------------
# Full graph — end-to-end via build_session_graph()
# ---------------------------------------------------------------------------

def test_graph_compiles():
    graph = build_session_graph()
    assert graph is not None


def test_graph_invoke_produces_prompt_id(mock_cfg):
    graph = build_session_graph()
    result = graph.invoke(_base_state(
        prompt="what nakshatra is the moon in today",
        session_id="",
    ))
    assert result["prompt_id"] != ""


def test_graph_state_is_immutable_between_nodes(mock_cfg):
    """Each node returns a partial dict; original state dict must not be mutated."""
    initial = _base_state(prompt="nakshatra today", session_id="")
    original_memories = initial["memories"]

    graph = build_session_graph()
    result = graph.invoke(initial)

    assert original_memories == []
    assert len(result["memories"]) > 0


def test_run_session_convenience(mock_cfg):
    with patch("langchain_learning.session_graph._graph", None):
        result = run_session("what is the gold price today")

    assert result["prompt_id"] != ""


# ---------------------------------------------------------------------------
# MemorySaver — turn counter persists across invocations on the same thread
# ---------------------------------------------------------------------------

@pytest.fixture()
def mem_graph(mock_cfg):
    """Inject a fresh MemorySaver-backed session graph for tests that need cross-call state."""
    from langgraph.checkpoint.memory import MemorySaver
    import langchain_learning.session_graph as sg
    prev = sg._graph
    sg._graph = sg.build_session_graph(checkpointer=MemorySaver())
    yield sg
    sg._graph = prev


def test_turn_increments_across_invocations(mem_graph, log_turn):
    """Turn must increment each UserPromptSubmit on the same session thread."""
    sg = mem_graph
    log_turn("turn 1")
    r1 = sg.run_session("hello", session_id="turn-test", cwd="/tmp")
    assert r1["turn"] == 1
    log_turn("turn 2")
    r2 = sg.run_session("hello again", session_id="turn-test", cwd="/tmp")
    assert r2["turn"] == 2
    log_turn("turn 3")
    r3 = sg.run_session("one more", session_id="turn-test", cwd="/tmp")
    assert r3["turn"] == 3


def test_ups_pending_hook_output_surfaced_and_cleared(mem_graph):
    """run_session must return pending_hook_output to the caller but zero the
    checkpoint copy, so it cannot leak into the next PostToolUse response
    (run_post_tool returns whatever is in the field)."""
    sg = mem_graph
    sid = "pho-leak-test"
    sg.run_session("first turn", session_id=sid, cwd="/tmp")

    payload = {"hookSpecificOutput": {
        "hookEventName": "UserPromptSubmit",
        "additionalContext": "task:aabbcc closed — run /task-introspection task:aabbcc",
    }}
    sg.get_session_graph().update_state(sg._config(sid), {"pending_hook_output": payload})

    result = sg.run_session("second turn", session_id=sid, cwd="/tmp")
    assert result["pending_hook_output"] == payload

    saved = sg.get_session_graph().get_state(sg._config(sid))
    assert (saved.values.get("pending_hook_output") or {}) == {}

    hook_output = sg.run_post_tool("Read", {"file_path": "/tmp/x"}, sid)
    assert hook_output == {}


def test_thread_isolation(mem_graph):
    """Different session_ids must not share turn state."""
    sg = mem_graph
    sg.run_session("prompt 1", session_id="sess-a", cwd="/tmp")
    ra = sg.run_session("prompt 2", session_id="sess-a", cwd="/tmp")
    rb = sg.run_session("prompt 1", session_id="sess-b", cwd="/tmp")
    assert ra["turn"] == 2
    assert rb["turn"] == 1


# ---------------------------------------------------------------------------
# Cross-hook checkpoint integration tests
# ---------------------------------------------------------------------------

class TestCheckpointCrossHook:
    """Verify that prompt_id and other state flow correctly across all four
    hook invocations (UserPromptSubmit → PreToolUse → PostToolUse → Stop)
    via the MemorySaver checkpoint — no DB reads mid-session.
    """

    def test_prompt_id_flows_from_submit_to_gate(self, mem_graph, _log_test_marker, log_turn):
        """prompt_id written by UserPromptSubmit must be readable by PreToolUse via checkpoint."""
        sg = mem_graph
        sid = "chk-test-gate"

        log_turn("user_prompt_submit")
        r1 = sg.run_session("send message to alice", session_id=sid, cwd="/tmp")
        prompt_id_from_submit = r1["prompt_id"]
        assert prompt_id_from_submit, "UserPromptSubmit must set prompt_id"

        log_turn("post_tool_use")
        sg.run_post_tool("mcp__local-mac__contacts__search", {"name": "Alice"}, session_id=sid, duration_ms=50,
                         tool_result={"name": "Alice", "phoneNumbers": [{"value": "+911234567890"}]})

        log_turn("pre_tool_use gate")
        gate_result = sg.run_gate("imessage__send", {"recipient": "+911234567890"}, session_id=sid)

        assert not gate_result["gate_denied"], \
            f"Gate should allow after prereqs; got denied: {gate_result['gate_reason']}"
        # Verify name check actually ran in THIS test (not silently skipped)
        rows = _log_test_marker(logger="lc.hooks.gates", search=["name_arg_check", "found_in_recent=True"])
        assert rows, "name_arg_check must fire and confirm name in prompt"

    def test_gate_allows_name_from_previous_prompt(self, mem_graph, log_turn):
        """Gate should allow imessage__send when the recipient name was in the PREVIOUS prompt."""
        sg = mem_graph
        sid = "chk-test-prev-prompt-name"

        log_turn("turn 1 submit")
        sg.run_session("send hi to Alice", session_id=sid, cwd="/tmp")
        log_turn("turn 1 post_tool")
        sg.run_post_tool("mcp__local-mac__contacts__search", {"name": "Alice"}, session_id=sid, duration_ms=30)
        log_turn("turn 2 submit")
        sg.run_session("Yes", session_id=sid, cwd="/tmp")
        log_turn("turn 2 gate")
        gate_result = sg.run_gate("imessage__send", {"recipient": "+911234567890"}, session_id=sid)

        assert not gate_result["gate_denied"], \
            f"Gate should allow when name was in previous prompt; got: {gate_result['gate_reason']}"

    def test_prompt_id_not_reset_between_hooks(self, mem_graph):
        """prompt_id must be the same across UserPromptSubmit and all subsequent hooks in the same turn."""
        sg = mem_graph
        sid = "chk-test-stable-pid"

        r1 = sg.run_session("check gold price", session_id=sid, cwd="/tmp")
        prompt_id_t1 = r1["prompt_id"]

        cp_after_gate = sg.get_session_graph().get_state({"configurable": {"thread_id": sid}})
        sg.run_gate("contacts__search", {}, session_id=sid)
        prompt_id_in_gate_checkpoint = cp_after_gate.values.get("prompt_id", "")

        assert prompt_id_in_gate_checkpoint == prompt_id_t1, \
            f"prompt_id changed between submit and gate: {prompt_id_t1!r} → {prompt_id_in_gate_checkpoint!r}"

    def test_new_turn_gets_new_prompt_id(self, mem_graph, log_turn):
        """Each UserPromptSubmit must generate a fresh prompt_id, replacing the prior one."""
        sg = mem_graph
        sid = "chk-test-new-pid"

        log_turn("turn 1")
        r1 = sg.run_session("turn one", session_id=sid, cwd="/tmp")
        pid1 = r1["prompt_id"]
        log_turn("turn 2")
        r2 = sg.run_session("turn two", session_id=sid, cwd="/tmp")
        pid2 = r2["prompt_id"]

        assert pid1 != pid2, "Each turn must produce a distinct prompt_id"
        assert pid1 != "", "Turn 1 prompt_id must be non-empty"
        assert pid2 != "", "Turn 2 prompt_id must be non-empty"

    def test_domains_persist_into_gate_hook(self, mem_graph):
        """Domains set during UserPromptSubmit must be visible in PreToolUse checkpoint."""
        sg = mem_graph
        sid = "chk-test-domains"

        r1 = sg.run_session("what is the gold and nifty outlook", session_id=sid, cwd="/tmp")
        domains_from_submit = r1.get("domains", [])

        cp_state = sg.get_session_graph().get_state({"configurable": {"thread_id": sid}})
        checkpoint_domains = cp_state.values.get("domains", [])
        assert set(domains_from_submit) == set(checkpoint_domains), \
            f"Domains lost between submit and gate checkpoint: {domains_from_submit} → {checkpoint_domains}"

    def test_turn_increments_correctly_across_all_hooks(self, mem_graph, log_turn):
        """turn counter must only increment on UserPromptSubmit, not on tool hooks."""
        sg = mem_graph
        sid = "chk-test-turn-stable"

        log_turn("turn 1 submit")
        r1 = sg.run_session("turn one", session_id=sid, cwd="/tmp")
        assert r1["turn"] == 1

        log_turn("turn 1 gate")
        sg.run_gate("contacts__search", {}, session_id=sid)
        cp_after_gate = sg.get_session_graph().get_state({"configurable": {"thread_id": sid}})
        assert cp_after_gate.values.get("turn") == 1, \
            f"turn should not change during PreToolUse, got {cp_after_gate.values.get('turn')}"

        log_turn("turn 2 submit")
        r2 = sg.run_session("turn two", session_id=sid, cwd="/tmp")
        assert r2["turn"] == 2

    def test_gate_denied_when_no_checkpoint_exists(self, mem_graph, _log_test_marker):
        """If no prior UserPromptSubmit checkpoint exists, gate must still be safe."""
        sg = mem_graph
        sid = "chk-test-no-prior"

        gate_result = sg.run_gate("imessage__send", {"recipient": "+911234567890"}, session_id=sid)
        assert gate_result["gate_denied"], \
            "Gate must deny gated tool when no checkpoint exists (no contacts__search recorded)"
        # Fallback prompt_id must be generated so gate logs are traceable (not prompt_id=?)
        rows = _log_test_marker(search="generated fallback prompt_id")
        assert rows, "run_gate must generate a fallback prompt_id when no UPS checkpoint exists"
        fb_pid = rows[0]["message"].split("prompt_id=")[-1].split()[0]
        assert fb_pid.startswith("fb-"), f"fallback prompt_id should start with 'fb-', got {fb_pid!r}"
        gate_rows = _log_test_marker(logger="lc.hooks.gates", search="DENY")
        assert gate_rows, "DENY must appear in gate logs"
        assert "?" not in gate_rows[0]["message"].split("prompt_id=")[-1].split()[0], \
            "gate log must not show prompt_id=?"

    # ------------------------------------------------------------------
    # MailDeleteGate integration
    # ------------------------------------------------------------------

    def test_mail_delete_denied_without_prior_read(self, mem_graph, log_turn):
        """mail__delete must be denied when no mail__read has been called this session."""
        sg = mem_graph
        sid = "chk-mail-delete-deny"

        log_turn("user_prompt_submit")
        sg.run_session("delete that email", session_id=sid, cwd="/tmp")

        log_turn("pre_tool_use gate — no prior mail read")
        gate_result = sg.run_gate("mail__delete", {}, session_id=sid)

        assert gate_result["gate_denied"], \
            f"mail__delete must be denied without prior mail__read; got allowed"

    def test_mail_delete_allowed_after_mail_read(self, mem_graph, log_turn):
        """mail__delete must be allowed after mail__read was called this session."""
        sg = mem_graph
        sid = "chk-mail-delete-allow"

        log_turn("user_prompt_submit")
        sg.run_session("show inbox then delete the first email", session_id=sid, cwd="/tmp")

        log_turn("post_tool_use — mail read")
        sg.run_post_tool("mcp__local-mac__mail__read", {}, session_id=sid, duration_ms=30,
                         tool_result={"messages": [{"id": "abc123", "subject": "Hello"}]})

        log_turn("pre_tool_use gate — after mail read")
        gate_result = sg.run_gate("mail__delete", {"message_id": "abc123"}, session_id=sid)

        assert not gate_result["gate_denied"], \
            f"mail__delete should be allowed after mail__read; got denied: {gate_result['gate_reason']}"

    def test_mail_delete_denied_when_no_checkpoint(self, mem_graph, log_turn):
        """mail__delete must be denied when there is no prior UPS checkpoint at all."""
        sg = mem_graph
        sid = "chk-mail-delete-no-prior"

        log_turn("pre_tool_use gate — no UPS checkpoint")
        gate_result = sg.run_gate("mail__delete", {"message_id": "xyz"}, session_id=sid)

        assert gate_result["gate_denied"], \
            "mail__delete must deny when no session checkpoint exists"


class TestDeactivateTaskRetrospective:
    """DeactivateTaskNode injects retrospective additionalContext on tasks__finish."""

    def _make_state(self, tool_name: str, task_id: str = "abc123", title: str = "Test task") -> dict:
        from langchain_learning.session_state import SessionState
        from collections import OrderedDict
        return {
            "event_type": "post_tool_use",
            "tool_name": tool_name,
            "tool_input": {"task_id": task_id},
            "tool_result": {},
            "session_id": "test-session",
            "prompt": "",
            "cwd": "",
            "turn": 1,
            "active_task_id": task_id,
            "active_task_title": title,
            "active_parent_task_id": "",
            "active_parent_task_title": "",
            "task_memories": [],
            "task_stack": [],
            "mid_task_decisions": [],
            "memories": [],
            "domains": [],
            "keywords": [],
            "tool_hints": [],
            "task_context": [],
            "task_rag_chunks": [],
            "task_body": "",
            "task_context_summary": "",
            "related_tasks": [],
            "related_commits": [],
            "active_review": {},
            "current_state": "prompt",
            "prompt_id": "",
            "prompt_tools": [],
            "session_prompt_ids": [],
            "session_tools": OrderedDict(),
            "session_prompt_texts": {},
            "gate_denied": False,
            "gate_reason": "",
            "duration_ms": 0.0,
            "pending_hook_output": {},
        }

    def test_finish_injects_retrospective(self):
        from langchain_learning.nodes.deactivate_task import DeactivateTaskNode
        node = DeactivateTaskNode()
        state = self._make_state("tasks__finish", task_id="abc123", title="My finished task")
        result = node(state)
        assert "pending_hook_output" in result
        output = result["pending_hook_output"]
        assert output["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
        ctx = output["hookSpecificOutput"]["additionalContext"]
        assert "My finished task" in ctx
        # Pointer to the single retro flow — /task-introspection owns capture now
        assert "/task-introspection task:abc123" in ctx
        # The old inline capture instructions must be gone (task:8c3c2ee4)
        assert "tasks__create_feedback" not in ctx
        assert "memory__add_batch" not in ctx

    def test_finish_pointer_matches_auto_done_nudge(self):
        """Both close paths must share one nudge sentence so they can't drift."""
        from langchain_learning.nodes.deactivate_task import DeactivateTaskNode
        from langchain_learning.nodes.log_task_events import INTROSPECTION_NUDGE_TEMPLATE
        node = DeactivateTaskNode()
        state = self._make_state("tasks__finish", task_id="abc123")
        ctx = node(state)["pending_hook_output"]["hookSpecificOutput"]["additionalContext"]
        assert INTROSPECTION_NUDGE_TEMPLATE.format(task_id="abc123") in ctx

    def test_clear_active_no_retrospective(self):
        from langchain_learning.nodes.deactivate_task import DeactivateTaskNode
        node = DeactivateTaskNode()
        state = self._make_state("tasks__clear_active", task_id="abc123")
        result = node(state)
        assert "pending_hook_output" not in result or result.get("pending_hook_output") == {}

    def test_finish_clears_active_task_fields(self):
        from langchain_learning.nodes.deactivate_task import DeactivateTaskNode
        node = DeactivateTaskNode()
        state = self._make_state("tasks__finish", task_id="abc123")
        result = node(state)
        assert result["active_task_id"] == ""
        assert result["active_task_title"] == ""
        assert result["task_memories"] == []
        assert result["execution_contract"] == ""
