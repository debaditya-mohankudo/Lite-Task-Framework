---
description: Run task-framework's test suite
---

Run the full test suite with `uv run pytest -q` and report the result as a
plain pass/fail count. Do not run a subset — `tests/test_concepts.py` and
`tests/test_models.py` bind modules to concepts and to the SysML model, so an
unrelated-looking change can break them.

If any test fails, show the failing test names and the relevant assertion
output. Do not summarize a failure as a pre-existing condition without
checking whether it actually is (e.g. `git stash` and re-run, or check
whether the same test fails on `main`).
