"""An eval session: the agent's runs of each golden case, shared by its metrics, and
what the session leaves when it ends."""

import json
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from marketplace_evals.config import EvalConfig
from marketplace_evals.evaluation import AgentRun, CaseResult, Metric, run_agent, score_runs
from marketplace_evals.goldens import GoldenCase
from marketplace_evals.paths import REPO_DIR
from marketplace_evals.reporting.results import results_document
from marketplace_evals.reporting.terminal import format_case_result, format_turns, format_usage
from marketplace_evals.runtimes import Judge, Runner
from marketplace_evals.usage import Usage, total


@dataclass(frozen=True)
class SessionSummary:
    """What a finished session prints and where it left its results."""

    turns: str
    usage: str
    files: list[Path]


class EvalSession:
    def __init__(self, config: EvalConfig, runner: Runner | None = None, judge: Judge | None = None):
        self.config = config
        self.runner = runner or config.new_runner()
        self.judge = judge or config.new_judge()
        self.runs_by_case: dict[str, list[AgentRun]] = {}
        self.results: list[CaseResult] = []
        self.started_at: datetime | None = None
        self._start = 0.0

    @property
    def started(self) -> bool:
        return self.started_at is not None

    def runs(self, case: GoldenCase) -> list[AgentRun]:
        """The agent's runs of `case`, launched the first time they are asked for."""
        if not self.started:
            self.started_at, self._start = datetime.now(), time.monotonic()
        if case.key not in self.runs_by_case:
            self.runs_by_case[case.key] = run_agent(case, self.runner, self.config.plugins_dir, self.config.runs)
        return self.runs_by_case[case.key]

    def score(self, case: GoldenCase, metric_name: str, metric: Metric) -> CaseResult:
        """Score the runs of `case` with `metric`, and keep the result for results.json."""
        result = score_runs(case, metric_name, metric, self.runs(case), self.config.min_passes)
        self.results.append(result)
        return result

    def agent_usage(self) -> Usage:
        return total([r.trace.usage for runs in self.runs_by_case.values() for r in runs if r.trace])

    def finish(self) -> SessionSummary:
        """Write `results.json` and `summary.txt` in the session's logs folder."""
        wall_clock_s = time.monotonic() - self._start
        turns = format_turns(self.runs_by_case)
        usage = format_usage(self.agent_usage(), self.judge.usage, wall_clock_s)
        logs_dir = self.config.logs_dir
        logs_dir.mkdir(parents=True, exist_ok=True)

        document = results_document(
            self.results, self.config, self.runner.version(), REPO_DIR,
            self.started_at, self.agent_usage(), self.judge.usage, wall_clock_s,
        )  # fmt: skip
        results_file = logs_dir / "results.json"
        results_file.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n")

        # What the terminal shows, which is lost when it closes: every report, then the tables.
        reports = "\n\n".join(f"{'PASSED' if r.passed else 'FAILED'} {format_case_result(r)}" for r in self.results)
        summary_file = logs_dir / "summary.txt"
        summary_file.write_text(f"{reports}\n\n== eval turns\n{turns}\n\n== eval usage\n{usage}\n")
        return SessionSummary(turns, usage, [results_file, summary_file])
