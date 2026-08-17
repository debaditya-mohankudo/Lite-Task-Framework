"""SysML model tests — the single-allocation criterion, enforced mechanically.

The model's whole acceptance criterion is that NO REQUIREMENT MAY BE SATISFIED
BY MORE THAN ONE PART. A second allocation means a rule and its data have
drifted apart, which is the defect this project's decomposition exists to
avoid.

A criterion stated in a model and checked by nothing is a comment: the model
stays plausible while the code drifts under it, and the drift is found only
when someone tries to extract a part and discovers it does not come away
clean. So this runs on every commit.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

MODELS = Path(__file__).parent.parent / "models"
EXPECTED = {"foundation.sysml", "requirements.sysml",
            "task_framework_system.sysml", "task_lifecycle.sysml",
            "derived_values.sysml"}

REQUIREMENT_DEF = re.compile(r"requirement\s+def\s+(\w+)")
SATISFY = re.compile(r"satisfy\s+requirement\s+(\w+)\s+by\s+([\w.]+)\s*;")
PART_DEF = re.compile(r"part\s+def\s+(\w+)")
#: `part <name> : <Type>;` — the composition inside System.
PART_INSTANCE = re.compile(r"part\s+(\w+)\s*:\s*(\w+)\s*;")


@pytest.fixture(scope="module")
def requirements() -> str:
    return (MODELS / "requirements.sysml").read_text()


@pytest.fixture(scope="module")
def system() -> str:
    return (MODELS / "task_framework_system.sysml").read_text()


class TestPresence:
    def test_every_model_file_exists(self):
        assert {p.name for p in MODELS.glob("*.sysml")} == EXPECTED


class TestSingleAllocation:
    def test_no_requirement_has_more_than_one_satisfy(self, requirements):
        """THE acceptance criterion. A second allocation is a design defect."""
        counts: dict[str, list[str]] = {}
        for name, part in SATISFY.findall(requirements):
            counts.setdefault(name, []).append(part)
        duplicates = {n: p for n, p in counts.items() if len(p) > 1}
        assert not duplicates, (
            "requirements allocated to more than one part — a rule and its data "
            f"have drifted apart: {duplicates}"
        )

    def test_every_requirement_is_allocated(self, requirements):
        defined = set(REQUIREMENT_DEF.findall(requirements))
        allocated = {name for name, _ in SATISFY.findall(requirements)}
        assert defined == allocated, (
            f"unallocated: {sorted(defined - allocated)}; "
            f"allocated but undefined: {sorted(allocated - defined)}"
        )

    def test_allocations_name_real_parts(self, requirements, system):
        """Every `by system.X` must match a part actually composed into System."""
        composed = {name for name, _ in PART_INSTANCE.findall(system)}
        for requirement, target in SATISFY.findall(requirements):
            assert target.startswith("system."), f"{requirement} allocated to {target}"
            part = target.split(".", 1)[1]
            assert part in composed, f"{requirement} allocated to unknown part {part!r}"


class TestParts:
    def test_the_four_parts_are_defined(self, system):
        assert set(PART_DEF.findall(system)) == {
            "TaskStore", "LifecycleRules", "TaskMCPTools", "System"
        }

    def test_no_part_is_named_for_a_specific_host(self, system):
        """Host names belong to instances, not to the structure.

        There is no host-specific part left to name — the one that existed
        (HostAdapter) was removed once every behaviour it carried either
        duplicated what TaskMCPTools already does explicitly, or could just be
        a skill's own step. This test stays as a guard against one reappearing.
        """
        for part in PART_DEF.findall(system):
            assert "ClaudeCode" not in part and "Claude" not in part, (
                f"part {part!r} names a specific host — host-specific behaviour "
                f"belongs in a skill, not a part"
            )

    def test_the_interface_depends_on_the_rules(self, system):
        """The structural form of 'rules cannot be bypassed'.

        If TaskMCPTools stopped composing LifecycleRules it could reach the
        store directly, and the project's central claim would quietly become
        false while every test still passed.
        """
        block = system.split("part def TaskMCPTools")[1].split("part def")[0]
        assert "rules : LifecycleRules" in block, "TaskMCPTools does not depend on the rules"

    def test_no_host_adapter_part_reappeared(self, system):
        """A host-specific adapter part was removed deliberately — see the
        package doc comment. If host-specific automaticity is wanted again,
        the argument is a skill's own step, not a part of this model."""
        assert "part def HostAdapter" not in system
        assert "part hooks" not in system

    def test_no_daemon_part_reappeared(self, system):
        """An earlier revision centred a daemon; it was abandoned deliberately."""
        assert "RuntimeServer" not in system
        assert "part def Daemon" not in system


class TestLifecycleMatchesCode:
    def test_modelled_states_match_the_implementation(self):
        from taskfw.models import STATUSES

        text = (MODELS / "task_lifecycle.sysml").read_text()
        for status in STATUSES:
            assert f"state {status}State" in text, f"{status} missing from the state model"

    def test_modelled_transitions_match_the_implementation(self):
        from taskfw.lifecycle import TERMINAL, TRANSITIONS

        text = (MODELS / "task_lifecycle.sysml").read_text()
        for current, targets in TRANSITIONS.items():
            for target in targets:
                name = f"{current}To{target.capitalize()}"
                assert name in text, f"transition {current}->{target} missing from the model"
        for terminal in TERMINAL:
            assert f"transition {terminal}To" not in text, f"{terminal} must have no transitions"


class TestDerivedValuesMatchCode:
    """Same construction as TestLifecycleMatchesCode: the calc def bodies in
    derived_values.sysml are not executed, so they are kept honest by
    importing the real Python source of truth and asserting the model's
    identifiers match it."""

    @pytest.fixture
    def text(self):
        return (MODELS / "derived_values.sysml").read_text()

    def test_progress_is_computed_not_stored(self, text):
        import inspect

        from taskfw.models import Task

        assert "calc def Progress" in text
        assert "return done" in text
        assert "return total" in text
        # progress must be a read-only property — a setter would mean the
        # value can be assigned independently of the resolution list it
        # derives from, which is exactly what the requirement forbids.
        assert isinstance(Task.progress, property)
        assert Task.progress.fset is None
        src = inspect.getsource(Task.progress.fget)
        assert "resolution" in src

    def test_memory_standing_values_match_the_implementation(self, text):
        import inspect

        from taskfw.memory import _standing

        assert "calc def MemoryStanding" in text
        src = inspect.getsource(_standing)
        returned = re.findall(r'return "(\w+)"', src)
        assert returned, "no return literals found in _standing — source may have changed shape"
        for standing in returned:
            assert standing in text, f"{standing} missing from MemoryStandingValue"

    def test_grooming_accuracy_grades_match_the_implementation(self, text):
        from taskfw.accuracy import GRADES

        assert "calc def GroomingAccuracyTally" in text
        for grade in GRADES:
            assert grade in text, f"{grade} missing from GroomingAccuracyTally"
