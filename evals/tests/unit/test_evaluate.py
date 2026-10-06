from pathlib import Path

from marketplace_evals.evaluation import AgentRun, run_agent, score_runs
from marketplace_evals.goldens import GoldenCase
from marketplace_evals.metrics import tool_correctness
from marketplace_evals.reporting.terminal import format_case_result, format_turns
from marketplace_evals.runtimes import AgentTask, Runner
from marketplace_evals.sandbox import Sandbox
from marketplace_evals.trace import Trace
from marketplace_evals.usage import Usage

CASE = GoldenCase("c", "plugin", "skill", Path("f"), "p", [], [])


class FakeRunner(Runner):
    name = "fake"

    def __init__(self, model: str, used: str):
        self.model, self.used = model, used

    def _launch(self, task: AgentTask, sandbox: Sandbox) -> Trace:
        raise NotImplementedError

    def run(self, task: AgentTask, root: Path) -> Trace:
        return Trace(runtime="fake", models={"main": {self.used}}, usage=Usage(calls=1))


def test_runs_on_another_model_are_errors_but_keep_their_usage():
    runs = run_agent(CASE, FakeRunner("claude-sonnet-5-5", "claude-sonnet-5"), Path("."), 2)

    assert all("model mismatch" in r.error for r in runs)
    assert all(r.trace.usage.calls == 1 for r in runs)

    result = score_runs(CASE, "tool_correctness", tool_correctness, runs, 1)
    assert result.passes == 0
    assert all(r.metric is None for r in result.runs)


def test_runs_on_the_pinned_model_are_scored():
    runs = run_agent(CASE, FakeRunner("claude-sonnet-5-5", "claude-sonnet-5-5"), Path("."), 2)
    assert all(r.error is None for r in runs)
    assert score_runs(CASE, "tool_correctness", tool_correctness, runs, 2).passed


def test_format_turns_has_a_row_per_run_and_columns_per_agent():
    trace = Trace(runtime="fake", turns={"main": 2, "rev": 3})
    table = format_turns({"case-a": [AgentRun(trace), AgentRun(None, "boom")]})
    lines = table.splitlines()
    assert "main turns/tools" in lines[0] and "rev turns/tools" in lines[0]
    assert lines[1].split()[:2] == ["case-a", "1"] and "2 / 0" in lines[1] and "3 / 0" in lines[1]
    assert "no trace: boom" in lines[2]


def test_case_reports_name_the_plugin_and_the_skill():
    runs = run_agent(CASE, FakeRunner("m", "m"), Path("."), 1)
    report = format_case_result(score_runs(CASE, "tool_correctness", tool_correctness, runs, 1))
    assert report.startswith("plugin/skill/c · tool_correctness: 1/1 runs OK")
