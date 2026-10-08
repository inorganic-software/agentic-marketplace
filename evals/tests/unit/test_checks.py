from pathlib import Path

from marketplace_evals.goldens import GoldenCase, GoldenCheck, PromptVariant
from marketplace_evals.metrics import outcome_checks, rules_always_met
from marketplace_evals.trace import CheckRun, Trace

CASE = GoldenCase(
    "c",
    "commons",
    "git-workflow",
    Path("f"),
    (PromptVariant("directo", "Commitea esto"),),
    [],
    [],
    rules_always_met=(GoldenCheck("main was not rewritten", "true"),),
    outcome_checks=(GoldenCheck("clean tree", "true"), GoldenCheck("feat branch", "true")),
)


def trace(*runs: CheckRun, changed: bool = True) -> Trace:
    return Trace(
        runtime="test",
        changed_files=["src/app.py"] if changed else [],
        check_runs={r.name: r for r in runs},
    )


def test_every_check_must_pass():
    result = outcome_checks(CASE, trace(CheckRun("clean tree", 0, ""), CheckRun("feat branch", 0, "feat/x")))
    assert result.passed
    assert result.score == 1.0
    assert [(c.id, c.passed) for c in result.checks] == [("clean tree", True), ("feat branch", True)]

    result = outcome_checks(CASE, trace(CheckRun("clean tree", 0, ""), CheckRun("feat branch", 1, "main")))
    assert not result.passed
    assert result.score == 0.5


def test_a_failed_check_shows_its_exit_code_and_output_but_keeps_its_name_as_key():
    result = outcome_checks(CASE, trace(CheckRun("clean tree", 0, ""), CheckRun("feat branch", 1, "fix/x")))
    failed = result.checks[1]
    assert failed.description == "feat branch — exit 1: fix/x"
    assert failed.id == "feat branch"


def test_a_check_that_did_not_run_fails():
    result = outcome_checks(CASE, trace(CheckRun("clean tree", 0, "")))
    assert not result.passed
    assert result.checks[1].description == "feat branch — not run"


def test_outcome_checks_fail_without_running_if_the_agent_changed_nothing():
    result = outcome_checks(CASE, trace(CheckRun("clean tree", 0, ""), CheckRun("feat branch", 0, ""), changed=False))
    assert not result.passed
    assert "changed no file" in result.error


def test_rules_always_met_count_even_if_the_agent_changed_nothing():
    """In a trap, doing nothing is right, and what was already there counts."""
    assert rules_always_met(CASE, trace(CheckRun("main was not rewritten", 0, ""), changed=False)).passed
    assert not rules_always_met(CASE, trace(CheckRun("main was not rewritten", 1, ""), changed=False)).passed
