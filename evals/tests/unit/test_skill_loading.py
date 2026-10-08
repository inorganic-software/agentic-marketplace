from pathlib import Path

import pytest

from marketplace_evals.goldens import GoldenCase, PromptVariant
from marketplace_evals.metrics import skill_loading
from marketplace_evals.trace import ToolCall, Trace


def case(should_load: bool) -> GoldenCase:
    return GoldenCase("c", "p", "s", Path("f"), (PromptVariant("v", "p"),), [], [], should_load=should_load)


def trace(*calls: ToolCall) -> Trace:
    return Trace(runtime="test", calls=list(calls))


LOADS = ToolCall("load_skill", {"name": "s"}, "Skill")
READS = ToolCall("read_file", {"path": "@plugins/p/skills/s/SKILL.md"}, "Read")


@pytest.mark.parametrize("call", [LOADS, READS, ToolCall("load_skill", {"name": "s"}, "Skill", denied=True)])
def test_loading_the_skill_by_name_or_reading_its_skill_md_counts(call):
    assert skill_loading(case(True), trace(call)).passed
    result = skill_loading(case(False), trace(call))
    assert not result.passed
    [check] = result.checks
    assert check.description.startswith("does not load the skill s — got main:")
    assert check.id == "does not load the skill"


@pytest.mark.parametrize(
    "call",
    [
        ToolCall("load_skill", {"name": "other"}, "Skill"),
        ToolCall("read_file", {"path": "@plugins/p/skills/s/references/x.md"}, "Read"),
        ToolCall("read_file", {"path": "@plugins/p/skills/other/SKILL.md"}, "Read"),
        ToolCall("shell", {"command": "cat @plugins/p/skills/s/SKILL.md"}, "Bash"),
    ],
)
def test_another_skill_or_another_file_is_not_loading_it(call):
    assert not skill_loading(case(True), trace(call)).passed
    assert skill_loading(case(False), trace(call)).passed


def test_without_calls_it_is_not_loaded():
    result = skill_loading(case(True), trace())
    assert (result.name, result.score, result.passed) == ("skill_loading", 0.0, False)
    assert [c.id for c in result.checks] == ["loads the skill"]
    assert skill_loading(case(False), trace()).passed
