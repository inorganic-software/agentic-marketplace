"""An eval session: the agent's runs of each golden case, shared by its metrics, and
what the session leaves when it ends."""

import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from marketplace_evals.config import EvalConfig
from marketplace_evals.efficiency import CaseEfficiency, Efficiency
from marketplace_evals.evaluation import (
    AgentRun,
    CaseResult,
    CaseStubGaps,
    case_baselines,
    run_agent,
    score_baseline,
    score_runs,
    skill_loading_summaries,
)
from marketplace_evals.goldens import GoldenCase
from marketplace_evals.metrics import MetricSpec
from marketplace_evals.paths import REPO_DIR
from marketplace_evals.reporting.results import results_document
from marketplace_evals.reporting.terminal import (
    format_baselines,
    format_case_result,
    format_efficiency,
    format_skill_loading,
    format_status,
    format_stub_gaps,
    format_usage,
)
from marketplace_evals.runtimes import Judge, Runner
from marketplace_evals.usage import Usage, total


@dataclass(frozen=True)
class SessionSummary:
    """What a finished session prints and where it left its results."""

    efficiency: str
    usage: str
    baseline: str | None  # each case with and without its skill, with a baseline
    skill_loading: str | None  # per skill, its `skill_loading` prompts that passed, if any ran
    stub_gaps: str | None  # per case, its runs with calls the fixture's stubs do not imitate, if any
    files: list[Path]


class EvalSession:
    def __init__(self, config: EvalConfig, runner: Runner | None = None, judge: Judge | None = None):
        self.config = config
        self.runner = runner or config.new_runner()
        self.judge = judge or config.new_judge()
        self.runs_by_case: dict[str, list[AgentRun]] = {}
        self.baseline_runs_by_case: dict[str, list[AgentRun]] = {}  # without the skill
        self.results: list[CaseResult] = []
        self.started_at: datetime | None = None
        self._start = 0.0

    @property
    def started(self) -> bool:
        return self.started_at is not None

    def runs(self, case: GoldenCase) -> list[AgentRun]:
        """The agent's runs of `case`, launched the first time they are asked for. With a
        baseline, the runs without the skill are launched at the same time, except for a
        `skill_loading` prompt: without the skill, the agent cannot load it."""
        if not self.started:
            self.started_at, self._start = datetime.now(), time.monotonic()
        if case.key not in self.runs_by_case:
            groups = (False, True) if self._with_baseline(case) else (False,)
            with ThreadPoolExecutor(max_workers=len(groups)) as pool:
                launched = list(
                    pool.map(
                        lambda without: run_agent(
                            case, self.runner, self.config.plugins_dir, self.config.runs, without_skill=without
                        ),
                        groups,
                    )
                )
            self.runs_by_case[case.key] = launched[0]
            if len(launched) > 1:
                self.baseline_runs_by_case[case.key] = launched[1]
        return self.runs_by_case[case.key]

    def _with_baseline(self, case: GoldenCase) -> bool:
        return self.config.baseline and not case.is_skill_loading

    def score(self, case: GoldenCase, metric_name: str, spec: MetricSpec) -> CaseResult:
        """Score the runs of `case` with the metric, and keep the result for results.json.
        With a baseline, the result also carries the metric with and without the skill."""
        metric, min_passes = spec.bind(self.judge), self.config.min_passes
        result = score_runs(case, metric_name, metric, self.runs(case), min_passes, spec.strict)
        if self._with_baseline(case):
            baseline = score_baseline(
                result,
                metric,
                self.runs(case),
                self.baseline_runs_by_case[case.key],
                min_passes,
                spec.on_calls,
                spec.applies_to,
            )
            result = CaseResult(result.case, metric_name, result.runs, result.required, result.strict, baseline)
        self.results.append(result)
        return result

    def agent_usage(self, without_skill: bool = False) -> Usage:
        """What every run with the skill spent, or every run without it; errors included."""
        runs_by_case = self.baseline_runs_by_case if without_skill else self.runs_by_case
        return total([r.trace.usage for runs in runs_by_case.values() for r in runs if r.trace])

    def efficiencies(self) -> list[CaseEfficiency]:
        """What each case's runs spent, in the order the cases ran."""
        cases = {r.case.key: r.case for r in self.results}
        return [
            CaseEfficiency(
                cases[key],
                Efficiency(runs),
                Efficiency(self.baseline_runs_by_case[key]) if key in self.baseline_runs_by_case else None,
            )
            for key, runs in self.runs_by_case.items()
            if key in cases
        ]

    def stub_gaps(self) -> list[CaseStubGaps]:
        """Per case, in the order the cases ran, its runs with calls the stubs do not imitate."""
        cases = {r.case.key: r.case for r in self.results}
        return [
            CaseStubGaps(cases[key], runs, self.baseline_runs_by_case.get(key))
            for key, runs in self.runs_by_case.items()
            if key in cases
        ]

    def finish(self) -> SessionSummary:
        """Write `results.json` and `summary.txt` in the session's logs folder."""
        wall_clock_s = time.monotonic() - self._start
        efficiencies = self.efficiencies()
        efficiency = format_efficiency(efficiencies)
        agent_without_skill = self.agent_usage(without_skill=True) if self.config.baseline else None
        usage = format_usage(self.agent_usage(), self.judge.usage, wall_clock_s, agent_without_skill)
        baseline = format_baselines(case_baselines(self.results)) if self.config.baseline else None
        summaries = skill_loading_summaries(self.results)
        skill_loading = format_skill_loading(summaries) if summaries else None
        stub_gaps = self.stub_gaps()
        gaps = format_stub_gaps(stub_gaps) if any(c.any for c in stub_gaps) else None
        logs_dir = self.config.logs_dir
        logs_dir.mkdir(parents=True, exist_ok=True)

        document = results_document(
            self.results, self.config, self.runner.version(), REPO_DIR,
            self.started_at, self.agent_usage(), self.judge.usage, wall_clock_s,
            efficiencies, agent_without_skill, stub_gaps,
        )  # fmt: skip
        results_file = logs_dir / "results.json"
        results_file.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n")

        # What the terminal shows, which is lost when it closes: every report, then the tables.
        reports = "\n\n".join(f"{format_status(r)} {format_case_result(r)}" for r in self.results)
        tables = f"== eval efficiency\n{efficiency}\n\n== eval usage\n{usage}\n"
        if baseline is not None:
            tables += f"\n== eval baseline\n{baseline}\n"
        if skill_loading is not None:
            tables += f"\n== eval skill loading\n{skill_loading}\n"
        if gaps is not None:
            tables += f"\n== eval stub gaps\n{gaps}\n"
        summary_file = logs_dir / "summary.txt"
        summary_file.write_text(f"{reports}\n\n{tables}")
        return SessionSummary(efficiency, usage, baseline, skill_loading, gaps, [results_file, summary_file])
