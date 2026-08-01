"""Skill tests — the host wrapper must not describe a system that does not exist.

The skills under .claude/skills are one host's delivery mechanism for the
methodology. They are prose, so most of what makes them correct is judgment —
but three things are mechanical, and all three have already failed once.

These skills were carried over verbatim from a system with a different tool
surface. They referenced roughly a dozen tools that do not exist here, a
session id the framework has no concept of, and a host-prefixed namespace that
would tie them to one host. Nothing caught it; a person reading the files did.

So: a skill naming a tool that is not registered fails the suite. That is the
enforcement, and it is the same bargain tests/test_concepts.py makes for
concept coverage — a check nobody runs is a comment.

What is NOT checked here is whether a skill's prose still agrees with its
methodology doc or with the other skills. That is judgment, and it belongs to
/task-skills-audit.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from taskfw import mcp_server as m

ROOT = Path(__file__).parent.parent
SKILLS = ROOT / ".claude" / "skills"
DOCS = ROOT / "docs" / "methodology"

#: `commit` is not a lifecycle stage. It is here because committing is the one
#: routine act that writes to the durable record, and its rules — cite a task,
#: never invent an id, report the test result as a number — are exactly the ones
#: that rot quietly. It shares 04-implementation.md rather than claiming a
#: document of its own, since the reasoning it applies is implementation's.
EXPECTED = {"commit", "discover-requirements", "task-create", "task-framework",
            "task-grooming", "task-implementation", "task-introspection", "task-skills-audit"}

#: Frontmatter every skill carries. `doc` is this repo's own convention: it
#: names the methodology document that owns the skill's reasoning, which is
#: what keeps the split between "doc owns why, skill owns how" auditable.
FRONTMATTER_FIELDS = ("name", "description", "user-invocable", "updated", "doc")

#: The audit skill is exempt from the banned-pattern checks, and only it.
#:
#: Its job is to hunt for exactly these patterns, so it names them as things to
#: look for rather than things to do. Scoping the ban to "no skill INSTRUCTS
#: this" and exempting the one file that enumerates the prohibitions is honest;
#: the alternatives are worse — a context-sniffing regex that guesses whether a
#: mention is an instruction, or a skill that cannot name what it audits.
AUDIT = "task-skills-audit"

#: task_memory__ is listed first so the alternation reaches it. Ordering it
#: after `tasks` would leave the namespace unchecked while the test still
#: passed — a blind spot in the very check that exists to prevent blind spots.
TOOL_REF = re.compile(r"\b(task_memory__\w+|tasks__\w+|concept__\w+)")
DOC_FIELD = re.compile(r"^doc:\s*(\S+)\s*$", re.MULTILINE)
NAME_FIELD = re.compile(r"^name:\s*(\S+)\s*$", re.MULTILINE)
#: Relative markdown links, excluding anchors and absolute URLs.
LINK = re.compile(r"\]\((?!https?:)([^)#]+)\)")


def skills() -> list[Path]:
    return sorted(SKILLS.glob("*/skill.md"))


def registered_tools() -> set[str]:
    """Taken from the server itself, never restated here."""
    return {n for n in dir(m) if n.startswith(("tasks__", "concept__", "task_memory__"))}


def frontmatter(path: Path) -> str:
    text = path.read_text()
    return text.split("---")[1] if text.startswith("---") else ""


class TestPresence:
    def test_every_skill_exists(self):
        assert {p.parent.name for p in skills()} == EXPECTED

    def test_none_are_stubs(self):
        for path in skills():
            assert len(path.read_text()) > 1000, f"{path.parent.name} is a stub"


class TestToolReferences:
    @pytest.mark.parametrize("path", skills(), ids=lambda p: p.parent.name)
    def test_every_referenced_tool_exists(self, path):
        """The check that would have caught the port.

        Six skills referenced create_scaffolded, update_document, link_tasks,
        history, index_task, and more — none of which exist here.
        """
        unknown = set(TOOL_REF.findall(path.read_text())) - registered_tools()
        assert not unknown, (
            f"{path.parent.name} references non-existent tools: {sorted(unknown)}"
        )

    @pytest.mark.parametrize(
        "path", [p for p in skills() if p.parent.name != AUDIT], ids=lambda p: p.parent.name
    )
    def test_no_host_prefixed_tool_names(self, path):
        """An mcp__<server>__ prefix ties the skill to one host's wiring."""
        assert "mcp__" not in path.read_text(), path.parent.name

    @pytest.mark.parametrize(
        "path", [p for p in skills() if p.parent.name != AUDIT], ids=lambda p: p.parent.name
    )
    def test_no_session_id(self, path):
        """The framework has no session concept — active task is workspace-scoped.

        A skill telling an agent to obtain a session id is describing a
        different system, and the instruction cannot be followed here — there
        is nothing to obtain.
        """
        assert "session_id" not in path.read_text(), path.parent.name

    def test_only_the_audit_skill_is_exempt(self):
        """Pins the exemption so it cannot quietly widen.

        An exemption list is a hole in an enforcement, and a hole nobody
        watches grows. This is the watch.
        """
        assert AUDIT in {p.parent.name for p in skills()}
        assert len([p for p in skills() if p.parent.name == AUDIT]) == 1


class TestFrontmatter:
    @pytest.mark.parametrize("path", skills(), ids=lambda p: p.parent.name)
    def test_required_fields_are_present(self, path):
        block = frontmatter(path)
        for field in FRONTMATTER_FIELDS:
            assert f"{field}:" in block, f"{path.parent.name} is missing {field}"

    @pytest.mark.parametrize("path", skills(), ids=lambda p: p.parent.name)
    def test_name_matches_the_directory(self, path):
        assert NAME_FIELD.search(frontmatter(path)).group(1) == path.parent.name

    @pytest.mark.parametrize("path", skills(), ids=lambda p: p.parent.name)
    def test_doc_points_at_a_real_methodology_file(self, path):
        """`doc` names the document that owns this skill's reasoning.

        A dangling pointer means the split between doc and skill has quietly
        become unowned.
        """
        target = ROOT / DOC_FIELD.search(frontmatter(path)).group(1)
        assert target.exists(), f"{path.parent.name} doc: points at missing {target}"

    def test_frontmatter_names_no_foreign_checkout(self):
        """Frontmatter must not point at a source outside this repo.

        A skill whose frontmatter names another checkout has two homes, and
        nothing keeps them in step — the copy that ships and the copy that runs
        drift apart silently.
        """
        for path in skills():
            block = frontmatter(path)
            assert "claude-hooks" not in block, (
                f"{path.parent.name} frontmatter names a checkout outside this repo"
            )


class TestLinks:
    @pytest.mark.parametrize("path", skills(), ids=lambda p: p.parent.name)
    def test_relative_links_resolve(self, path):
        for target in LINK.findall(path.read_text()):
            assert (path.parent / target).resolve().exists(), (
                f"{path.parent.name} links to missing {target}"
            )


class TestCoverage:
    def test_each_lifecycle_stage_has_a_skill(self):
        """One skill per methodology document, plus the audit.

        The five documents ARE the loop, so a stage with no skill is a stage
        with no way to invoke it on this host.
        """
        docs = {p.name for p in DOCS.glob("0*.md")}
        referenced = {
            Path(DOC_FIELD.search(frontmatter(p)).group(1)).name for p in skills()
        }
        assert docs <= referenced, f"methodology docs with no skill: {sorted(docs - referenced)}"

    def test_the_cross_task_tool_is_referenced_somewhere(self):
        """A tool nobody is told to call is a tool nobody calls.

        tasks__grooming_accuracy is the one tool with no natural discovery
        path: it is not part of working a single task, so nothing surfaces it
        unless a skill says to run it.
        """
        assert any("tasks__grooming_accuracy" in p.read_text() for p in skills())
