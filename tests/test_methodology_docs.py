"""Methodology doc tests — cross-references and tool names must stay real.

These docs are the product, and their most likely failure is silent: a renamed
tool leaves the prose describing an API that no longer exists, and nothing
fails. The prior system needed a dedicated audit skill to catch exactly this
class of drift; a test is cheaper and runs every time.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from taskfw import mcp_server as m

DOCS = Path(__file__).parent.parent / "docs" / "methodology"
EXPECTED = {
    "README.md", "01-framework.md", "02-create.md",
    "03-grooming.md", "04-implementation.md", "05-introspection.md",
}

#: Any task-side tool token appearing in prose or code blocks. task_memory__
#: is matched first — otherwise the alternation would never reach it, and a
#: whole namespace would go unchecked while the test still passed.
TOOL_REF = re.compile(r"\b(?:task_memory__|tasks__)[a-z_]+\b")
#: Markdown links to sibling documents.
DOC_LINK = re.compile(r"\]\((?!https?:)([^)#]+\.md)[^)]*\)")


def docs() -> list[Path]:
    return sorted(DOCS.glob("*.md"))


def registered_tools() -> set[str]:
    """Tool names taken from the module itself, not restated here."""
    return {n for n in dir(m) if n.startswith(("tasks__", "task_memory__"))}


class TestPresence:
    def test_every_document_exists(self):
        assert {p.name for p in docs()} == EXPECTED

    def test_none_are_empty(self):
        for path in docs():
            assert len(path.read_text().strip()) > 500, f"{path.name} is a stub"


class TestToolReferences:
    @pytest.mark.parametrize("path", docs(), ids=lambda p: p.name)
    def test_every_referenced_tool_exists(self, path):
        referenced = set(TOOL_REF.findall(path.read_text()))
        unknown = referenced - registered_tools()
        assert not unknown, f"{path.name} references non-existent tools: {sorted(unknown)}"

    def test_docs_do_not_use_the_old_host_prefixed_names(self):
        """A server-prefixed tool name ties the docs to one host's wiring.

        The methodology is the portable asset; naming the server a tool happens
        to be registered under makes it unusable anywhere that server is not.
        """
        for path in docs():
            assert "mcp__claude-hooks__" not in path.read_text(), path.name

    def test_docs_do_not_require_a_session_id(self):
        """The framework has no session concept — active task is workspace-scoped."""
        for path in docs():
            assert "session_id" not in path.read_text(), path.name

    def test_the_headline_tool_is_documented(self):
        readme = (DOCS / "README.md").read_text()
        assert "tasks__context" in readme


class TestCrossReferences:
    @pytest.mark.parametrize("path", docs(), ids=lambda p: p.name)
    def test_internal_links_resolve(self, path):
        for target in DOC_LINK.findall(path.read_text()):
            assert (path.parent / target).exists(), f"{path.name} links to missing {target}"

    def test_readme_indexes_every_document(self):
        readme = (DOCS / "README.md").read_text()
        for path in docs():
            if path.name != "README.md":
                assert path.name in readme, f"README does not index {path.name}"


class TestSimplifiedModel:
    def test_docs_do_not_describe_removed_issue_types(self):
        """Two types only — story/bug/subtask were deliberately removed."""
        for path in docs():
            text = path.read_text().lower()
            for removed in ("subtask parent must", "story parent", "issue_type"):
                assert removed not in text, f"{path.name} describes the old type system"

    def test_pull_not_push_is_stated_explicitly(self):
        """The one thing a reader from an injection-based system must know."""
        readme = (DOCS / "README.md").read_text().lower()
        assert "pull" in readme and "inject" in readme
