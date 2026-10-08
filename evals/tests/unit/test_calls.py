from pathlib import Path

from marketplace_evals.goldens import GoldenCase, PromptVariant
from marketplace_evals.matchers import AnyOf, CallMatcher
from marketplace_evals.metrics import expected_calls, forbidden_calls
from marketplace_evals.trace import ToolCall, Trace

CASE = GoldenCase(
    id="c",
    plugin="p",
    skill="s",
    fixture_dir=Path("f"),
    prompts=(PromptVariant("v", "p"),),
    expected_calls=[
        CallMatcher("read_file", {"path": "src/x.java"}),
        CallMatcher("shell", {"command": "*mvn*"}),
    ],
    forbidden_calls=[CallMatcher("edit_file"), CallMatcher("read_file", {"path": "@plugins/*"})],
)


def trace(*calls: ToolCall) -> Trace:
    return Trace(runtime="test", calls=list(calls))


def test_all_checks_pass():
    calls = trace(
        ToolCall("read_file", {"path": "src/x.java"}, "Read"),
        ToolCall("shell", {"command": "mvn compile"}, "Bash", denied=True),
    )
    for result in (expected_calls(CASE, calls), forbidden_calls(CASE, calls)):
        assert result.score == 1.0
        assert result.passed


def test_missing_expected_call_fails():
    result = expected_calls(CASE, trace(ToolCall("read_file", {"path": "src/x.java"}, "Read")))
    assert result.name == "expected_calls"
    assert result.score == 1 / 2
    assert not result.passed


def test_forbidden_call_fails_only_forbidden_calls():
    calls = trace(
        ToolCall("read_file", {"path": "src/x.java"}, "Read"),
        ToolCall("shell", {"command": "mvn compile"}, "Bash"),
        ToolCall("read_file", {"path": "@plugins/p/skills/s/SKILL.md"}, "Read"),
    )
    result = forbidden_calls(CASE, calls)
    assert result.name == "forbidden_calls"
    assert [c.passed for c in result.checks] == [True, False]
    assert not result.passed
    assert expected_calls(CASE, calls).passed


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
    result = forbidden_calls(CASE, trace(*[ToolCall("edit_file", {"path": f"f{i}"}, "Edit") for i in range(5)]))
    forbidden = result.checks[0].description
    assert "main: edit_file(path='f0')" in forbidden and "+2 more" in forbidden


def shell(command: str) -> ToolCall:
    return ToolCall("shell", {"command": command}, "Bash")


def test_a_shell_pattern_matches_one_simple_command_not_the_whole_line():
    push_to_main = CallMatcher("shell", {"command": "*git push*main*"})
    # A branch pushed and its PR opened against main, in one line: no push to main.
    pr = "git switch -c feat/x && git commit -m 'feat: x' && git push -u origin HEAD && gh pr create --base main"

    assert not push_to_main.matches(shell(pr))
    assert push_to_main.matches(shell("git add . && git commit -m x && git push origin main"))


def test_match_and_not_match_look_at_the_same_simple_command():
    plain_pull = CallMatcher("shell", {"command": "*git pull*"}, not_match={"command": ["*--rebase*", "*--ff-only*"]})

    assert plain_pull.matches(shell("git pull origin main && git rebase --continue"))
    assert not plain_pull.matches(shell("git fetch && git pull --rebase origin main"))


def test_separators_inside_quotes_do_not_split():
    push = CallMatcher("shell", {"command": "git push*"})
    assert not push.matches(shell('git commit -m "fix: a && git push origin main"'))
    assert push.matches(shell("git commit -m 'a; b' ; git push"))
