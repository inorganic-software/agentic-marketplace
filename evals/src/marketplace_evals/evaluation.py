"""Evaluation loop.

The agent is not deterministic: each golden case is run N times, only once, and each
metric scores those same runs separately. A metric passes the case if at least
`min_passes` of the N runs pass it.
"""

import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from marketplace_evals.goldens import GoldenCase
from marketplace_evals.metrics import Metric, MetricResult
from marketplace_evals.runtimes import AgentTask, Runner
from marketplace_evals.trace import Trace
from marketplace_evals.usage import model_mismatch


@dataclass(frozen=True)
class AgentRun:
    trace: Trace | None  # kept even on error when there is one, for the usage summary
    error: str | None = None  # the run does not count: it is not scored


@dataclass(frozen=True)
class RunResult:
    run: AgentRun
    metric: MetricResult | None  # None when the run had an error

    @property
    def passed(self) -> bool:
        return self.metric is not None and self.metric.passed


@dataclass(frozen=True)
class CaseResult:
    """One metric over every run of a golden case."""

    case: GoldenCase
    metric_name: str
    runs: list[RunResult]
    min_passes: int

    @property
    def passes(self) -> int:
        return sum(r.passed for r in self.runs)

    @property
    def passed(self) -> bool:
        return self.passes >= self.min_passes


def run_agent(case: GoldenCase, runner: Runner, plugins_dir: Path, runs: int) -> list[AgentRun]:
    """Run the case `runs` times in parallel, each in its own sandbox."""
    task = AgentTask(
        prompt=case.prompt,
        plugin_dir=plugins_dir / case.plugin,
        fixture_dir=case.fixture_dir,
        tools=case.tools,
        inspect=case.inspect,
    )
    with ThreadPoolExecutor(max_workers=runs) as pool:
        return list(pool.map(lambda i: _run_once(case, runner, task, i), range(runs)))


def score_runs(case: GoldenCase, metric_name: str, metric: Metric, runs: list[AgentRun], min_passes: int) -> CaseResult:
    """Score every run with `metric`, in parallel. A run with an error is not scored."""

    def score(run: AgentRun) -> RunResult:
        return RunResult(run, None if run.error else metric(case, run.trace))

    with ThreadPoolExecutor(max_workers=len(runs)) as pool:
        return CaseResult(case, metric_name, list(pool.map(score, runs)), min_passes)


def _run_once(case: GoldenCase, runner: Runner, task: AgentTask, i: int) -> AgentRun:
    with tempfile.TemporaryDirectory(prefix=f"{case.key.replace('/', '-')}-{i}-") as root:
        try:
            trace = runner.run(task, Path(root))
        except Exception as e:  # a broken run counts as a failure, it does not abort the rest
            return AgentRun(None, f"{type(e).__name__}: {e}")
    # A run on a model other than the pinned one is not comparable: it does not count.
    for agent, models in trace.models.items():
        if error := model_mismatch(models, runner.model):
            return AgentRun(trace, f"{agent}: {error} (log={trace.raw_log})")
    return AgentRun(trace)
