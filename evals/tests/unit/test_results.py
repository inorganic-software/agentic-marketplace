import json
from datetime import datetime
from pathlib import Path

from marketplace_evals.config import EvalConfig
from marketplace_evals.evaluation import AgentRun, CaseResult, RunResult
from marketplace_evals.goldens import GoldenCase
from marketplace_evals.metrics import Check, MetricResult
from marketplace_evals.reporting.results import results_document
from marketplace_evals.trace import Trace
from marketplace_evals.usage import Usage

CASE = GoldenCase("case-a", "commons", "git-workflow", Path("f"), "p", [], [])
CONFIG = EvalConfig(provider="claude", backend="default", model="m", judge_model="j", runs=3, min_passes=2)


def run(*checks: Check, error: str | None = None) -> RunResult:
    trace = Trace(runtime="t", raw_log="/logs/x.jsonl")
    if error:
        return RunResult(AgentRun(trace, error), None)
    score = sum(c.passed for c in checks) / len(checks)
    return RunResult(AgentRun(trace), MetricResult("m", score, 1.0, list(checks)))


def document(result: CaseResult) -> dict:
    return results_document(
        [result],
        CONFIG,
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
        "tool_correctness",
        [
            run(Check("does not call x — got a", False, key="does not call x"), Check("calls y", True)),
            run(Check("does not call x", True, key="does not call x"), Check("calls y", True)),
            run(error="model mismatch"),
        ],
        min_passes=2,
    )
    metric = document(result)["cases"]["commons/git-workflow/case-a"]["metrics"]["tool_correctness"]

    assert metric["checks"] == {
        "does not call x": {"passed": 1, "scored": 2},
        "calls y": {"passed": 2, "scored": 2},
    }
    assert (metric["passes"], metric["runs"], metric["passed"]) == (1, 3, False)
    assert metric["per_run"][2] == {
        "passed": False,
        "score": None,
        "error": "model mismatch",
        "log": "/logs/x.jsonl",
    }


def test_records_what_the_session_ran_with_and_is_json():
    doc = document(CaseResult(CASE, "rules", [run(Check("r", True))], min_passes=1))
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
