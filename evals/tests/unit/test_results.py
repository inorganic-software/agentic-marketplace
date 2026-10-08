import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from marketplace_evals.config import EvalConfig
from marketplace_evals.evaluation import AgentRun, Baseline, CaseResult, RunResult
from marketplace_evals.goldens import GoldenCase, PromptVariant
from marketplace_evals.metrics import Check, MetricResult
from marketplace_evals.reporting.results import results_document
from marketplace_evals.trace import Trace
from marketplace_evals.usage import Usage

CASE = GoldenCase("case-a", "commons", "git-workflow", Path("f"), (PromptVariant("v", "p"),), [], [])
CONFIG = EvalConfig(provider="claude", backend="default", model="m", judge_model="j", runs=3, min_passes=2)


def run(*checks: Check, error: str | None = None) -> RunResult:
    trace = Trace(runtime="t", raw_log="/logs/x.jsonl")
    if error:
        return RunResult(AgentRun(trace, error), None)
    score = sum(c.passed for c in checks) / len(checks)
    return RunResult(AgentRun(trace), MetricResult("m", score, 1.0, list(checks)))


def document(*results: CaseResult, config: EvalConfig = CONFIG) -> dict:
    return results_document(
        list(results),
        config,
        "2.1.285",
        Path("."),
        datetime(2026, 9, 30, 12, 0),
        Usage(calls=3, cost_usd=1.23456, models={"m"}),
        Usage(),
        60.04,
    )


def test_checks_are_aggregated_across_runs_under_their_stable_key():
    result = CaseResult(
        CASE,
        "forbidden_calls",
        [
            run(Check("does not call x — got a", False, key="does not call x"), Check("calls y", True)),
            run(Check("does not call x", True, key="does not call x"), Check("calls y", True)),
            run(error="model mismatch"),
        ],
        required=3,
        strict=True,
    )
    metric = document(result)["cases"]["commons/git-workflow/case-a"]["metrics"]["forbidden_calls"]

    assert metric["checks"] == {
        "does not call x": {"passed": 1, "scored": 2},
        "calls y": {"passed": 2, "scored": 2},
    }
    assert (metric["passes"], metric["runs"], metric["passed"]) == (1, 3, False)
    assert (metric["required"], metric["strict"]) == (3, True)
    assert metric["per_run"][2] == {
        "variant": None,
        "passed": False,
        "score": None,
        "error": "model mismatch",
        "log": "/logs/x.jsonl",
    }


def test_records_what_the_session_ran_with_and_is_json():
    doc = document(CaseResult(CASE, "rules_always_met_judged", [run(Check("r", True))], required=1))
    assert doc["config"]["runtime_version"] == "2.1.285"
    assert doc["config"]["model"] == "m"
    assert doc["config"]["backend"] == "default"
    assert doc["started_at"] == "2026-09-30T12:00:00"
    assert doc["usage"]["agent"]["cost_usd"] == 1.2346
    assert doc["usage"]["agent"]["models"] == ["m"]
    assert set(doc["repo"]) == {"commit", "branch", "dirty"}
    case = doc["cases"]["commons/git-workflow/case-a"]
    assert (case["plugin"], case["skill"]) == ("commons", "git-workflow")
    json.dumps(doc)


def test_with_a_baseline_each_metric_and_case_records_how_it_did_without_the_skill():
    def metric(name: str, *passed: bool) -> CaseResult:
        return CaseResult(CASE, name, [run(Check("x", p)) for p in passed], required=2)

    outcome = replace(metric("outcome", True, True, True), baseline=Baseline(
        metric("outcome", True, True, True), metric("outcome", True, False, False)
    ))  # fmt: skip
    calls = replace(metric("expected_calls", True, True, True), baseline=Baseline(None, None))
    doc = document(outcome, calls, config=replace(CONFIG, baseline=True))

    assert doc["schema_version"] == 8 and doc["config"]["baseline"] is True
    case = doc["cases"]["commons/git-workflow/case-a"]
    assert case["baseline"] == {"with_skill": True, "without_skill": False}
    b = case["metrics"]["outcome"]["baseline"]
    assert (b["with_skill"]["passes"], b["without_skill"]["passes"], b["delta"]) == (3, 1, 0.6667)
    assert b["without_skill"]["checks"] == {"x": {"passed": 1, "scored": 3}}
    assert case["metrics"]["expected_calls"]["baseline"] is None  # only the skill can pass it


def test_without_a_baseline_nothing_about_it_is_recorded():
    doc = document(CaseResult(CASE, "outcome", [run(Check("x", True))], required=1))
    assert doc["config"]["baseline"] is False
    case = doc["cases"]["commons/git-workflow/case-a"]
    assert "baseline" not in case and "baseline" not in case["metrics"]["outcome"]


def test_skill_loading_prompts_say_what_they_expect_and_are_summed_up_per_skill():
    prompt = replace(CASE, id="should-not-load-x", should_load=False)
    doc = document(
        CaseResult(prompt, "skill_loading", [run(Check("x", True)), run(Check("x", False))], required=1),
        CaseResult(CASE, "outcome", [run(Check("x", True))], required=1),
    )

    assert doc["cases"]["commons/git-workflow/should-not-load-x"]["should_load"] is False
    assert "should_load" not in doc["cases"]["commons/git-workflow/case-a"]
    assert doc["skill_loading"] == {
        "commons/git-workflow": {
            "should_load": {"prompts": 0, "prompts_passed": 0, "runs": 0, "runs_passed": 0},
            "should_not_load": {"prompts": 1, "prompts_passed": 1, "runs": 2, "runs_passed": 1},
        }
    }


def test_without_skill_loading_prompts_its_summary_is_empty():
    assert document(CaseResult(CASE, "outcome", [run(Check("x", True))], required=1))["skill_loading"] == {}


def test_an_informative_case_records_it_and_which_of_its_metrics_only_report():
    case = replace(CASE, informative=True)
    doc = document(
        CaseResult(case, "outcome_checks", [run(Check("c", False))], required=1),
        CaseResult(case, "rules_always_met", [run(Check("r", True))], required=1, strict=True),
    )
    entry = doc["cases"]["commons/git-workflow/case-a"]
    assert entry["informative"] is True
    assert entry["metrics"]["outcome_checks"]["informative"] is True
    assert entry["metrics"]["rules_always_met"]["informative"] is False
    required = document(CaseResult(CASE, "outcome_checks", [run(Check("c", True))], required=1))
    assert required["cases"]["commons/git-workflow/case-a"]["informative"] is False


def test_each_outcome_check_records_whether_it_is_required():
    doc = document(
        CaseResult(
            CASE,
            "outcome",
            [
                run(
                    Check("[required] a — ok", True, key="a", required=True),
                    Check("b — no", False, key="b", required=False),
                )
            ],
            required=2,
        ),
        CaseResult(CASE, "outcome_checks", [run(Check("c", True))], required=2),
    )
    metrics = doc["cases"]["commons/git-workflow/case-a"]["metrics"]
    assert metrics["outcome"]["checks"] == {
        "a": {"passed": 1, "scored": 1, "required": True},
        "b": {"passed": 0, "scored": 1, "required": False},
    }
    # Metrics that do not tell required from optional record nothing about it.
    assert metrics["outcome_checks"]["checks"] == {"c": {"passed": 1, "scored": 1}}
    assert doc["schema_version"] == 8


VARIANTS = replace(CASE, prompts=(PromptVariant("a", "A"), PromptVariant("b", "B"), PromptVariant("c", "C")))


def variant_run(variant: str, passed: bool) -> RunResult:
    trace = Trace(runtime="t", raw_log="/logs/x.jsonl")
    return RunResult(AgentRun(trace, variant=variant), MetricResult("m", float(passed), 1.0, [Check("x", passed)]))


def test_each_run_records_its_variant_and_each_metric_the_runs_per_variant():
    def metric(*passed: bool) -> CaseResult:
        return CaseResult(VARIANTS, "outcome", [variant_run("ab"[i % 2], p) for i, p in enumerate(passed)], 2)

    with_skill = metric(True, False, True)
    result = replace(with_skill, baseline=Baseline(with_skill, metric(False, False, True)))
    doc = document(result, config=replace(CONFIG, baseline=True))
    m = doc["cases"]["commons/git-workflow/case-a"]["metrics"]["outcome"]
    assert [r["variant"] for r in m["per_run"]] == ["a", "b", "a"]
    assert m["variants"] == {
        "a": {"passes": 2, "runs": 2},
        "b": {"passes": 0, "runs": 1},
        "c": {"passes": 0, "runs": 0},
    }
    assert m["baseline"]["without_skill"]["variants"]["a"] == {"passes": 1, "runs": 2}
    assert m["baseline"]["variant_deltas"] == {"a": 0.5, "b": 0, "c": None}


def test_a_case_with_one_variant_records_no_breakdown():
    doc = document(CaseResult(CASE, "outcome", [variant_run("v", True)], 1))
    m = doc["cases"]["commons/git-workflow/case-a"]["metrics"]["outcome"]
    assert "variants" not in m and m["per_run"][0]["variant"] == "v"
