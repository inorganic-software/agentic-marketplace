from dataclasses import replace
from datetime import datetime
from pathlib import Path

from marketplace_evals.config import EvalConfig
from marketplace_evals.efficiency import CaseEfficiency, Delta, Efficiency, run_measures
from marketplace_evals.evaluation import AgentRun, CaseResult
from marketplace_evals.goldens import GoldenCase, PromptVariant
from marketplace_evals.reporting.compare import build_matrix
from marketplace_evals.reporting.markdown import render
from marketplace_evals.reporting.results import results_document
from marketplace_evals.reporting.terminal import format_efficiency, format_measure_delta, format_usage
from marketplace_evals.trace import ToolCall, Trace
from marketplace_evals.usage import Usage

CASE = GoldenCase("case-a", "commons", "git-workflow", Path("f"), (PromptVariant("v", "p"),), [], [])
CONFIG = EvalConfig(provider="claude", backend="default", model="m", judge_model="j", runs=3, min_passes=2)


def run(turns: int, tokens: int, cost: float = 0.0, error: str | None = None, tools: int = 0) -> AgentRun:
    trace = Trace(
        runtime="t",
        turns={"main": turns - 1, "sub": 1},
        calls=[ToolCall("run_shell", {}, "bash")] * tools,
        usage=Usage(input_tokens=tokens, cost_usd=cost, duration_s=10.0 * turns, calls=1),
    )
    return AgentRun(trace, error, variant="v")


def test_a_run_adds_up_its_agents_and_has_no_cost_its_runtime_does_not_report():
    measures = run_measures(run(5, 100, tools=3))
    assert (measures["turns"], measures["tool_calls"], measures["tokens"]) == (5, 3, 100)
    assert measures["cost_usd"] is None and measures["ai_credits"] is None
    assert run_measures(AgentRun(None, "boom")) == dict.fromkeys(measures)


def test_the_median_leaves_out_the_runs_with_errors():
    group = Efficiency([run(8, 150, 0.2), run(9, 160, 0.3), run(25, 600, 1.0), run(99, 9999, 9.0, error="model")])
    median = group.median()
    assert len(group.valid) == 3
    assert (median["turns"], median["tokens"], median["cost_usd"]) == (9, 160, 0.3)
    assert median["ai_credits"] is None
    assert Efficiency([run(1, 1, error="boom")]).median()["turns"] is None


def test_the_delta_is_with_the_skill_minus_without_and_has_no_percent_over_zero():
    e = CaseEfficiency(CASE, Efficiency([run(9, 160)]), Efficiency([run(6, 0)]))
    delta = e.delta()
    assert delta["turns"] == Delta(3, 50.0)
    assert delta["tokens"] == Delta(160, None)
    assert delta["cost_usd"] is None
    assert CaseEfficiency(CASE, Efficiency([run(9, 160)])).delta() == {}


def test_delta_text():
    assert format_measure_delta("turns", Delta(3, 50.0)) == "+3 (+50 %)"
    assert format_measure_delta("duration_s", Delta(-65, -20.0)) == "-1m05s (-20 %)"
    assert format_measure_delta("tokens", Delta(0, 0.0)) == "0 (0 %)"
    assert format_measure_delta("tokens", Delta(160, None)) == "+160"
    assert format_measure_delta("tokens", None) == "–"


def test_the_table_has_a_row_per_case_and_with_a_baseline_without_the_skill_and_the_delta():
    e = CaseEfficiency(
        CASE, Efficiency([run(9, 160_000), run(10, 200_000, error="boom")]), Efficiency([run(6, 95_000)])
    )
    lines = format_efficiency([e]).splitlines()
    assert "turns" in lines[0] and "cost $" not in lines[0]  # no run reported a cost
    assert lines[1].split()[:4] == ["commons/git-workflow/case-a", "1/2", "9", "0"]
    assert "160,000" in lines[1]
    assert lines[2].split()[:4] == ["without", "skill", "1/1", "6"]
    assert lines[3].split()[0] == "Δ" and "+3 (+50 %)" in lines[3] and "+65,000 (+68 %)" in lines[3]


def test_the_usage_table_has_a_row_for_the_runs_without_the_skill():
    table = format_usage(Usage(calls=3, input_tokens=10), Usage(), 1.0, Usage(calls=3, input_tokens=5))
    rows = [line.split()[0] for line in table.splitlines()[1:-1]]
    assert rows == ["agent", "agent", "judge", "total"]
    assert "agent without skill" in table and table.splitlines()[-2].split()[2] == "15"


def document(*efficiencies: CaseEfficiency, baseline: bool = False) -> dict:
    return results_document(
        [CaseResult(CASE, "outcome", [], required=1)],
        replace(CONFIG, baseline=baseline),
        None,
        Path("."),
        datetime(2026, 10, 8),
        Usage(calls=3, input_tokens=100),
        Usage(calls=1, input_tokens=7),
        60.0,
        list(efficiencies),
        Usage(calls=3, input_tokens=40) if baseline else None,
    )


def test_results_record_each_case_efficiency_and_the_usage_without_the_skill():
    e = CaseEfficiency(CASE, Efficiency([run(9, 160, 0.25), run(1, 1, error="boom")]), Efficiency([run(6, 80, 0.2)]))
    doc = document(e, baseline=True)

    assert doc["schema_version"] == 9
    efficiency = doc["cases"]["commons/git-workflow/case-a"]["efficiency"]
    assert (efficiency["with_skill"]["runs"], efficiency["with_skill"]["total_runs"]) == (1, 2)
    assert efficiency["with_skill"]["median"]["turns"] == 9
    assert efficiency["with_skill"]["per_run"][1]["error"] == "boom"
    assert efficiency["without_skill"]["median"]["tokens"] == 80
    assert efficiency["delta"]["tokens"] == {"absolute": 80, "percent": 100.0}
    assert efficiency["delta"]["ai_credits"] is None
    assert doc["usage"]["agent_without_skill"]["input_tokens"] == 40

    without = document(CaseEfficiency(CASE, Efficiency([run(9, 160)])))
    assert set(without["cases"]["commons/git-workflow/case-a"]["efficiency"]) == {"with_skill"}
    assert "agent_without_skill" not in without["usage"]


def test_the_pr_comment_counts_the_runs_without_the_skill_in_the_usage():
    doc = document(baseline=True)
    doc["repo"] = {"commit": "abc", "dirty": False}
    assert "agent 140 in 6 runs" in render(doc)


def test_compare_has_a_row_per_measure_with_and_without_the_skill():
    e = CaseEfficiency(CASE, Efficiency([run(9, 160, 0.25)]), Efficiency([run(6, 80, 0.2)]))
    new = document(e, baseline=True) | {"_name": "new"}
    old = document() | {"_name": "old"}  # before schema 8: no efficiency
    golden = build_matrix([new, old])["goldens"][0]

    rows = {row["name"]: row["cells"] for row in golden["efficiency"]}
    assert list(rows) == ["turns", "tool calls", "tokens", "cost $", "time"]  # no AI credits reported
    assert rows["turns"][0] == {"text": "9", "note": "w/o 6 · Δ +3 (+50 %)"}
    assert rows["turns"][1] is None
