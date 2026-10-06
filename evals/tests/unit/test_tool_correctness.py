from pathlib import Path

from marketplace_evals.goldens import GoldenCase
from marketplace_evals.matchers import AnyOf, CallMatcher
from marketplace_evals.metrics import tool_correctness
from marketplace_evals.trace import ToolCall, Trace

CASE = GoldenCase(
    id="c",
    plugin="p",
    skill="s",
    fixture_dir=Path("f"),
    prompt="p",
    expected_calls=[
        CallMatcher("read_file", {"path": "src/x.java"}),
        CallMatcher("shell", {"command": "*mvn*"}),
    ],
    forbidden_calls=[CallMatcher("edit_file"), CallMatcher("read_file", {"path": "@plugins/*"})],
)


def trace(*calls: ToolCall) -> Trace:
    return Trace(runtime="test", calls=list(calls))


def test_all_checks_pass():
    result = tool_correctness(
        CASE,
        trace(
            ToolCall("read_file", {"path": "src/x.java"}, "Read"),
            ToolCall("shell", {"command": "mvn compile"}, "Bash", denied=True),
        ),
    )
    assert result.score == 1.0
    assert result.passed


def test_missing_expected_call_fails():
    result = tool_correctness(CASE, trace(ToolCall("read_file", {"path": "src/x.java"}, "Read")))
    assert result.score == 3 / 4
    assert not result.passed


def test_forbidden_call_fails():
    result = tool_correctness(
        CASE,
        trace(
            ToolCall("read_file", {"path": "src/x.java"}, "Read"),
            ToolCall("shell", {"command": "mvn compile"}, "Bash"),
            ToolCall("read_file", {"path": "@plugins/p/skills/s/SKILL.md"}, "Read"),
        ),
    )
    assert [c.passed for c in result.checks] == [True, True, True, False]


def test_matcher_without_args_matches_any_call_of_that_action():
    assert CallMatcher("edit_file").matches(ToolCall("edit_file", {"path": "a"}, "Edit"))
    assert not CallMatcher("edit_file").matches(ToolCall("write_file", {"path": "a"}, "Write"))


def test_matcher_with_by_only_counts_calls_from_that_agent():
    matcher = CallMatcher("read_file", {"path": "src/x.java"}, by="reviewer")
    assert matcher.matches(ToolCall("read_file", {"path": "src/x.java"}, "Read", by="reviewer"))
    assert not matcher.matches(ToolCall("read_file", {"path": "src/x.java"}, "Read"))


def test_not_match_only_matches_calls_outside_its_globs():
    outside = CallMatcher("write_file", not_match={"path": ["*/domain/*", "*/application/*"]})
    assert not outside.matches(ToolCall("write_file", {"path": "src/a/domain/model/X.java"}, "Write"))
    assert not outside.matches(ToolCall("write_file", {"path": "src/a/application/useCase/U.java"}, "Write"))
    assert outside.matches(ToolCall("write_file", {"path": "src/a/infrastructure/A.java"}, "Write"))
    assert outside.matches(ToolCall("write_file", {"path": "pom.xml"}, "Write"))


def test_any_of_passes_with_either_matcher():
    skill = AnyOf(
        [
            CallMatcher("read_file", {"path": "@plugins/p/skills/s/SKILL.md"}),
            CallMatcher("load_skill", {"name": "s"}),
        ]
    )
    assert skill.matches(ToolCall("read_file", {"path": "@plugins/p/skills/s/SKILL.md"}, "Read"))
    assert skill.matches(ToolCall("load_skill", {"name": "s"}, "Skill"))
    assert not skill.matches(ToolCall("load_skill", {"name": "other"}, "Skill"))


def test_forbidden_check_shows_the_offending_calls():
    result = tool_correctness(CASE, trace(*[ToolCall("edit_file", {"path": f"f{i}"}, "Edit") for i in range(5)]))
    forbidden = result.checks[2].description
    assert "main: edit_file(path='f0')" in forbidden and "+2 more" in forbidden
