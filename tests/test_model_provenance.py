"""
Enforces the @ModelProvenance stamps in models/*.sysml, and separately
checks that any taskfw/*.py or tests/*.py path named in doc-comment prose
still exists.

The SysML model is prose about code, and prose rots silently: a requirement
goes on citing a file long after that file's behaviour changed, and nothing
notices. Each package therefore declares the code it describes
(modelledPaths) and the commit it was written against (derivedFromCommit),
and this module checks both against git.

What a failure here means: nobody has re-read the model since the modelled
code moved. It does NOT mean the model is now wrong. The fix is to re-read
the package against the change and then bump its stamp — bumping the stamp
without re-reading converts a useful signal into a lie, which is worse than
having no stamp at all.

See models/foundation.sysml::ModelProvenance for the rationale behind
stamping the modelled-code commit rather than the .sysml file's own commit.

modelledPaths is a package's DECLARED citations. A doc comment can also name
a file in running prose without declaring it there — task:4e2d5f28 found
two such citations (taskfw/dispatcher.py, tests/test_models.py) that had
gone stale after a split and a rename, caught only by a human re-reading the
package by hand. test_prose_cited_paths_still_resolve below is the
mechanical version of that same check.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
MODEL_DIR = REPO_ROOT / "models"

# The application, not the definition: `metadata def ModelProvenance` in
# foundation.sysml declares the attributes and carries no values, so keying
# on the leading '@' is what separates the two.
_STAMP_RE = re.compile(r"@ModelProvenance\s*\{(.*?)\}", re.DOTALL)
_COMMIT_RE = re.compile(r"derivedFromCommit\s*=\s*\"([0-9a-fA-F]+)\"")
_PATHS_RE = re.compile(r"modelledPaths\s*=\s*\((.*?)\)", re.DOTALL)
_QUOTED_RE = re.compile(r"\"([^\"]+)\"")

# Doc-comment prose, not the @ModelProvenance block above: a citation here is
# freeform and never validated against modelledPaths, so it needs its own check.
_DOC_COMMENT_RE = re.compile(r"doc\s*/\*(.*?)\*/", re.DOTALL)
_PROSE_PATH_RE = re.compile(r"\b(?:taskfw|tests)/[A-Za-z0-9_/]+\.py\b")


def _git(*args: str) -> str | None:
    """Run git at the repo root, returning stripped stdout or None on failure."""
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


@pytest.fixture(scope="module")
def in_git_checkout() -> bool:
    return _git("rev-parse", "--git-dir") is not None


def _parse_stamp(path: Path) -> tuple[str, list[str]]:
    text = path.read_text()

    stamp = _STAMP_RE.search(text)
    assert stamp, f"{path.name} has no @ModelProvenance stamp"
    body = stamp.group(1)

    commit = _COMMIT_RE.search(body)
    assert commit, f"{path.name} stamp has no derivedFromCommit"

    paths_block = _PATHS_RE.search(body)
    assert paths_block, f"{path.name} stamp has no modelledPaths"
    paths = _QUOTED_RE.findall(paths_block.group(1))
    assert paths, f"{path.name} declares an empty modelledPaths"

    return commit.group(1), paths


def _model_files() -> list[Path]:
    return sorted(MODEL_DIR.glob("*.sysml"))


def _prose_cited_paths(path: Path) -> set[str]:
    """Every taskfw/*.py or tests/*.py-shaped token in this file's doc comments.

    Scoped to text inside doc /* ... */ blocks, not the whole file, so this
    never re-checks what modelledPaths already declares — the two are
    distinct citation mechanisms, and conflating them would just duplicate
    test_modelled_paths_still_resolve under a different name.
    """
    text = path.read_text()
    doc_text = "\n".join(m.group(1) for m in _DOC_COMMENT_RE.finditer(text))
    return set(_PROSE_PATH_RE.findall(doc_text))


def test_model_dir_is_not_empty():
    """Guards the parametrized tests below: an empty glob passes vacuously."""
    assert _model_files(), f"no .sysml files found under {MODEL_DIR}"


@pytest.mark.parametrize("model_file", _model_files(), ids=lambda p: p.name)
def test_every_package_carries_a_stamp(model_file: Path):
    commit, paths = _parse_stamp(model_file)
    assert len(commit) >= 7, f"{model_file.name}: derivedFromCommit {commit!r} is too short to be unambiguous"


@pytest.mark.parametrize("model_file", _model_files(), ids=lambda p: p.name)
def test_modelled_paths_still_resolve(model_file: Path):
    """
    A cited path that no longer exists is drift the commit check cannot catch:
    git log over a deleted path reports the deletion commit, which may well
    match the stamp, so the model would look current while describing a file
    that is gone.
    """
    _, paths = _parse_stamp(model_file)
    missing = [p for p in paths if not (REPO_ROOT / p).exists()]
    assert not missing, (
        f"{model_file.name} declares modelledPaths that no longer exist: "
        f"{', '.join(missing)}.\n"
        "Either the model is describing code that was renamed or removed, or "
        "the path list is stale. Fix the model, then update modelledPaths."
    )


@pytest.mark.parametrize("model_file", _model_files(), ids=lambda p: p.name)
def test_prose_cited_paths_still_resolve(model_file: Path):
    """
    task:4e2d5f28 — a doc comment can name a taskfw/*.py or tests/*.py file in
    running prose without ever declaring it in modelledPaths, so
    test_modelled_paths_still_resolve never sees it. A file split (e.g.
    taskfw/dispatcher.py into taskfw/dispatcher/) or renamed outright (e.g.
    tests/test_models.py to tests/test_sysml.py) leaves that prose stale with
    nothing to catch it until someone reads the package by hand.
    """
    missing = sorted(p for p in _prose_cited_paths(model_file) if not (REPO_ROOT / p).exists())
    assert not missing, (
        f"{model_file.name} doc comments cite paths that no longer exist: "
        f"{', '.join(missing)}.\n"
        "The file was renamed or removed and the prose needs updating to "
        "match — or, if the text was never meant as a citation, reword it "
        "so it doesn't read like a real path."
    )


@pytest.mark.parametrize("model_file", _model_files(), ids=lambda p: p.name)
def test_stamp_matches_last_commit_touching_modelled_code(model_file: Path, in_git_checkout: bool):
    if not in_git_checkout:
        pytest.skip("not a git checkout — provenance cannot be verified")

    stamped, paths = _parse_stamp(model_file)

    actual = _git("log", "-1", "--format=%H", "--", *paths)
    if not actual:
        pytest.skip(
            f"git log returned no commit for {model_file.name}'s modelledPaths "
            "(shallow clone or grafted history)"
        )

    stamped_full = _git("rev-parse", f"{stamped}^{{commit}}")
    if stamped_full is None:
        pytest.skip(
            f"{model_file.name} is stamped {stamped}, which is not present in this "
            "history (shallow clone)"
        )

    subject = _git("log", "-1", "--format=%s", actual) or ""
    assert stamped_full == actual, (
        f"{model_file.name} is stamped {stamped}, but the code it models has "
        f"changed since.\n"
        f"  last commit touching its modelledPaths: {actual[:7]}  {subject}\n"
        f"Re-read this package against that change. If the model is still "
        f"accurate, bump derivedFromCommit to {actual[:7]}; if it is not, fix "
        f"the model first.\n"
        f"Do not bump the stamp without re-reading — that turns the signal "
        f"into a lie."
    )
