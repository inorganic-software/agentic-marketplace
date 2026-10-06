import json
from pathlib import Path

from marketplace_evals.goldens import GoldenCase
from marketplace_evals.metrics import outcome, rules
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
    "Commitea esto",
    [],
    [],
    expected_outcome=["The branch is feat/...", "The message is conventional", "main is untouched"],
    rules=["No commit lands on main", "main is never force-pushed"],
)
INSPECTED = GoldenCase(**{**CASE.__dict__, "inspect": ["git log --oneline"]})


def trace(changed: bool = True, initial_state: str = "", final_state: str = "") -> Trace:
    files = {
        "README.md": "# app",
        "src/app.py": "print('hi')",
    }
    return Trace(
        runtime="test",
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


def test_rules_needs_every_rule():
    assert rules(CASE, trace(), FakeJudge(answer(True, True))).passed
    assert not rules(CASE, trace(), FakeJudge(answer(True, False))).passed


def test_rules_are_judged_even_when_the_agent_changed_nothing():
    # In a trap, changing nothing is right; and what was already there counts.
    judge = FakeJudge(answer(False, True))
    result = rules(CASE, trace(changed=False), judge)
    assert not result.passed
    assert result.error is None
    assert "--- src/app.py (unchanged)" in judge.prompts[0]
    assert "1. No commit lands on main\n2. main is never force-pushed" in judge.prompts[0]
