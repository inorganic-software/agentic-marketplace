import json
from dataclasses import replace
from pathlib import Path

from marketplace_evals.goldens import Criterion, GoldenCase, PromptVariant
from marketplace_evals.metrics import outcome, rules_always_met_judged
from marketplace_evals.runtimes import Judge, JudgeError
from marketplace_evals.trace import Trace


class FakeJudge(Judge):
    """Returns fixed answers, in order, and records the prompts it receives."""

    name = "fake"

    def __init__(self, *answers: str | Exception):
        super().__init__("fake")
        self.answers = list(answers)
        self.prompts: list[str] = []

    def ask(self, prompt: str, json_schema: dict | None = None) -> str:
        self.prompts.append(prompt)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


CASE = GoldenCase(
    "c",
    "commons",
    "git-workflow",
    Path("f"),
    (PromptVariant("directo", "Commitea esto"),),
    [],
    [],
    expected_outcome=[
        Criterion("The branch is feat/..."),
        Criterion("The message is conventional"),
        Criterion("main is untouched"),
    ],
    rules_always_met_judged=["No commit lands on main", "main is never force-pushed"],
)
INSPECTED = GoldenCase(**{**CASE.__dict__, "inspect": ["git log --oneline"]})


def trace(changed: bool = True, initial_state: str = "", final_state: str = "") -> Trace:
    files = {
        "README.md": "# app",
        "src/app.py": "print('hi')",
    }
    return Trace(
        runtime="test",
        prompt="Commitea esto",
        final_output="Done",
        final_files=files,
        changed_files=["src/app.py"] if changed else [],
        initial_state=initial_state,
        final_state=final_state,
    )


def answer(*met: bool) -> str:
    return json.dumps({"criteria": [{"index": i, "met": m, "reason": f"reason {i}"} for i, m in enumerate(met, 1)]})


def test_outcome_passes_with_at_most_one_miss():
    assert outcome(CASE, trace(), FakeJudge(answer(True, True, True))).passed
    result = outcome(CASE, trace(), FakeJudge(answer(True, False, True)))
    assert result.passed
    assert result.score == 2 / 3
    assert [c.passed for c in result.checks] == [True, False, True]
    assert "reason 2" in result.checks[1].description
    assert not outcome(CASE, trace(), FakeJudge(answer(False, False, True))).passed


def test_outcome_with_a_single_criterion_must_meet_it():
    """One tolerated miss of one criterion would let every run pass."""
    single = GoldenCase(**{**CASE.__dict__, "expected_outcome": [Criterion("The description is imperative")]})
    assert outcome(single, trace(), FakeJudge(answer(True))).passed
    result = outcome(single, trace(), FakeJudge(answer(False)))
    assert not result.passed
    assert result.threshold == 1.0


MIXED = GoldenCase(
    **{
        **CASE.__dict__,
        "expected_outcome": [
            Criterion("The conflict was resolved by intent", required=True),
            Criterion("The description is imperative"),
            Criterion("The scope names the module"),
        ],
    }
)


def test_a_missed_required_criterion_fails_the_run_even_if_it_is_the_only_miss():
    result = outcome(MIXED, trace(), FakeJudge(answer(False, True, True)))
    assert not result.passed
    assert result.score >= result.threshold  # one miss would be tolerated if it were optional
    assert result.missed_required == [result.checks[0]]


def test_with_the_required_criteria_met_one_optional_miss_is_tolerated_and_two_are_not():
    assert outcome(MIXED, trace(), FakeJudge(answer(True, False, True))).passed
    assert not outcome(MIXED, trace(), FakeJudge(answer(True, False, False))).passed


def test_one_required_and_one_optional_tolerates_missing_the_optional():
    pair = GoldenCase(**{**CASE.__dict__, "expected_outcome": MIXED.expected_outcome[:2]})
    assert outcome(pair, trace(), FakeJudge(answer(True, False))).passed
    assert not outcome(pair, trace(), FakeJudge(answer(False, True))).passed


def test_required_criteria_are_marked_in_the_report_but_not_for_the_judge():
    judge = FakeJudge(answer(True, True, True))
    result = outcome(MIXED, trace(), judge)
    assert "[required]" not in judge.prompts[0]
    assert result.checks[0].description.startswith("[required] The conflict was resolved by intent — ")
    assert [(c.key, c.required) for c in result.checks] == [
        ("The conflict was resolved by intent", True),
        ("The description is imperative", False),
        ("The scope names the module", False),
    ]


def test_judged_rules_do_not_tell_required_from_optional():
    """Every rule must be met: their checks are neither marked nor counted as required."""
    result = rules_always_met_judged(CASE, trace(), FakeJudge(answer(True, True)))
    assert [c.required for c in result.checks] == [None, None]
    assert not any(c.description.startswith("[required]") for c in result.checks)


def test_judge_sees_the_task_every_file_marked_the_message_and_the_criteria():
    judge = FakeJudge(answer(True, True, True))
    outcome(CASE, trace(), judge)
    prompt = judge.prompts[0]
    assert "Commitea esto" in prompt
    assert "--- src/app.py (changed by the agent)\nprint('hi')" in prompt
    # Unchanged files are sent too, so criteria like "X stays as it was" can be checked.
    assert "--- README.md (unchanged)\n# app" in prompt
    assert "Done" in prompt
    assert "1. The branch is feat/...\n2. The message is conventional\n3. main is untouched" in prompt
    assert "State before" not in prompt  # the golden inspects nothing


def test_judge_sees_the_state_before_and_after_when_the_golden_inspects_it():
    judge = FakeJudge(answer(True, True, True))
    outcome(INSPECTED, trace(initial_state="$ git log\na1 init", final_state="$ git log\nb2 feat: x"), judge)
    prompt = judge.prompts[0]
    assert "State before the agent started (output of each command):\n$ git log\na1 init" in prompt
    assert "State after the agent finished (output of the same commands):\n$ git log\nb2 feat: x" in prompt


def test_a_change_only_in_the_inspected_state_counts_as_work():
    # A commit changes no file in the working tree, only the history.
    judge = FakeJudge(answer(True, True, True))
    assert outcome(INSPECTED, trace(changed=False, initial_state="a", final_state="b"), judge).passed


def test_nothing_changed_fails_without_calling_the_judge():
    judge = FakeJudge()
    result = outcome(INSPECTED, trace(changed=False, initial_state="a", final_state="a"), judge)
    assert not result.passed
    assert "changed no file" in result.error
    assert judge.prompts == []


def test_judge_is_retried_once_then_run_fails():
    assert outcome(CASE, trace(), FakeJudge(JudgeError("timeout"), answer(True, True, True))).passed

    result = outcome(CASE, trace(), FakeJudge(JudgeError("timeout"), "not json"))
    assert not result.passed
    assert result.error.startswith("JUDGE ERROR:")


def test_answer_missing_a_criterion_is_a_judge_error():
    result = outcome(CASE, trace(), FakeJudge(answer(True, True), answer(True, True)))
    assert result.error.startswith("JUDGE ERROR:")


def test_rules_always_met_judged_needs_every_rule():
    assert rules_always_met_judged(CASE, trace(), FakeJudge(answer(True, True))).passed
    assert not rules_always_met_judged(CASE, trace(), FakeJudge(answer(True, False))).passed


def test_rules_always_met_judged_are_judged_even_when_the_agent_changed_nothing():
    # In a trap, changing nothing is right; and what was already there counts.
    judge = FakeJudge(answer(False, True))
    result = rules_always_met_judged(CASE, trace(changed=False), judge)
    assert not result.passed
    assert result.error is None
    assert "--- src/app.py (unchanged)" in judge.prompts[0]
    assert "1. No commit lands on main\n2. main is never force-pushed" in judge.prompts[0]


def test_judge_sees_the_variant_the_run_got_not_the_first_one():
    judge = FakeJudge(answer(True, True, True))
    case = replace(CASE, prompts=(*CASE.prompts, PromptVariant("coloquial", "commitea esto porfa")))
    outcome(case, replace(trace(), prompt="commitea esto porfa"), judge)
    assert "Task given to the agent:\ncommitea esto porfa\n" in judge.prompts[0]
