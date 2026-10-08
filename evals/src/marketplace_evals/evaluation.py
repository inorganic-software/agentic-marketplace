"""Evaluation loop.

The agent is not deterministic: each golden case is run N times, only once, and each
metric scores those same runs separately. A metric passes the case if at least
`min_passes` of the N runs pass it, or all N if it is strict (a prohibition). A run
with an error counts as not passed. In an informative case, a metric that is not strict
is scored the same way, but whether it passes is only reported.

With a baseline, each case is also run N times without its skill, and each metric
scores those runs too. The call metrics score both groups without what only the skill
can pass (loading it, reading its SKILL.md). The difference between the pass rates is
what the skill adds. It is informative: whether the case passes is decided by the runs
with the skill alone.

A case with several prompt variants gives run i the variant i mod n, with and without
the skill alike, and reports each variant's pass rate next to the total that decides.

The golden's `skill_loading` prompts are cases too, scored by their own metric, and
also summed up per skill: how many of the prompts that should load it do, and how many
of those that should not, do not.
"""

import tempfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
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
    variant: str | None = None  # id of the prompt variant it ran


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
    required: int  # runs that must pass: `min_passes`, or all of them if strict
    strict: bool = False
    baseline: Baseline | None = None  # with a baseline: the same metric with and without the skill

    @property
    def passes(self) -> int:
        return sum(r.passed for r in self.runs)

    @property
    def passed(self) -> bool:
        return self.passes >= self.required

    @property
    def rate(self) -> float:
        return self.passes / len(self.runs) if self.runs else 0.0

    @property
    def informative(self) -> bool:
        """A quality metric of an informative case: failing it does not fail the session."""
        return self.case.informative and not self.strict

    def by_variant(self) -> dict[str, VariantTally]:
        """Runs that passed per prompt variant, in the golden's order. A variant no run
        got (fewer runs than variants) has 0 runs."""
        tallies = {v.id: VariantTally() for v in self.case.prompts}
        for r in self.runs:
            if r.run.variant in tallies:
                tallies[r.run.variant] = tallies[r.run.variant].add(r.passed)
        return tallies


@dataclass(frozen=True)
class VariantTally:
    """The runs of one prompt variant, and how many passed."""

    passes: int = 0
    runs: int = 0

    def add(self, passed: bool) -> VariantTally:
        return VariantTally(self.passes + passed, self.runs + 1)

    @property
    def rate(self) -> float | None:
        return self.passes / self.runs if self.runs else None


@dataclass(frozen=True)
class Baseline:
    """A metric of a case with the skill and without it, both scored without what only
    the skill can pass. Both are None if that leaves the metric nothing to score (every
    expected call is loading the skill, say): only the skill can pass it."""

    with_skill: CaseResult | None
    without_skill: CaseResult | None

    @property
    def comparable(self) -> bool:
        return self.without_skill is not None

    @property
    def delta(self) -> float | None:
        """Pass rate with the skill minus without it, from -1 to 1."""
        if self.with_skill is None or self.without_skill is None:
            return None
        return self.with_skill.rate - self.without_skill.rate

    def variant_deltas(self) -> dict[str, float | None]:
        """Per prompt variant, its pass rate with the skill minus without it; None for a
        variant no run got. Both groups give run i the same variant."""
        if self.with_skill is None or self.without_skill is None:
            return {}
        without = self.without_skill.by_variant()
        return {
            variant: None if tally.rate is None or without[variant].rate is None else tally.rate - without[variant].rate
            for variant, tally in self.with_skill.by_variant().items()
        }


@dataclass(frozen=True)
class CaseBaseline:
    """Whether a case passes with its skill and without it, on the metrics both can be
    scored on. A case that passes without the skill does not measure it."""

    case: GoldenCase
    passed_with: bool
    passed_without: bool


def run_agent(
    case: GoldenCase, runner: Runner, plugins_dir: Path, runs: int, without_skill: bool = False
) -> list[AgentRun]:
    """Run the case `runs` times in parallel, each in its own sandbox, run i with the
    variant i mod n of its prompts. With `without_skill`, the case's skill is left out of
    the plugin: a baseline run."""
    task = AgentTask(
        prompt="",
        plugin_dir=plugins_dir / case.plugin,
        fixture_dir=case.fixture_dir,
        tools=case.tools,
        inspect=case.inspect,
        before=case.before,
        checks=(*case.rules_always_met, *case.outcome_checks),
        without_skill=case.skill if without_skill else None,
    )
    prefix = f"{case.key.replace('/', '-')}-{'baseline-' if without_skill else ''}"

    def run(i: int) -> AgentRun:
        variant = case.variant(i)
        name = f"{prefix}{variant.id.replace('/', '-') + '-' if case.has_variants else ''}{i}-"
        return replace(_run_once(runner, replace(task, prompt=variant.text), name), variant=variant.id)

    with ThreadPoolExecutor(max_workers=runs) as pool:
        return list(pool.map(run, range(runs)))


def score_runs(
    case: GoldenCase, metric_name: str, metric: Metric, runs: list[AgentRun], min_passes: int, strict: bool = False
) -> CaseResult:
    """Score every run with `metric`, in parallel. A run with an error is not scored.
    A strict metric needs every run to pass, whatever `min_passes` is."""
    required = len(runs) if strict else min_passes

    def score(run: AgentRun) -> RunResult:
        return RunResult(run, None if run.error else metric(case, run.trace))

    with ThreadPoolExecutor(max_workers=len(runs)) as pool:
        return CaseResult(case, metric_name, list(pool.map(score, runs)), required, strict)


def score_baseline(
    result: CaseResult,
    metric: Metric,
    runs_with: list[AgentRun],
    runs_without: list[AgentRun],
    min_passes: int,
    on_calls: bool = False,
    applies_to: Callable[[GoldenCase], bool] = lambda case: True,
) -> Baseline:
    """`result` (the metric on the runs with the skill) compared with the runs without
    it. A metric `on_calls` scores both on the case without the calls only the skill can
    make, and has no baseline if that leaves it nothing to score (`applies_to`). Any
    other metric reuses `result`, so a judged one is not judged twice."""
    case = result.case.comparable() if on_calls else result.case
    if not applies_to(case):
        return Baseline(None, None)
    with_skill = (
        result
        if case == result.case
        else score_runs(case, result.metric_name, metric, runs_with, min_passes, result.strict)
    )
    without_skill = score_runs(case, result.metric_name, metric, runs_without, min_passes, result.strict)
    return Baseline(with_skill, without_skill)


def case_baselines(results: list[CaseResult]) -> list[CaseBaseline]:
    """Per case, in the order of `results`, whether it passes with and without its skill:
    every metric both can be scored on must pass. Cases with no such metric are left out."""
    by_case: dict[str, list[Baseline]] = {}
    cases: dict[str, GoldenCase] = {}
    for result in results:
        if result.baseline is not None and result.baseline.comparable:
            by_case.setdefault(result.case.key, []).append(result.baseline)
            cases[result.case.key] = result.case
    return [
        CaseBaseline(
            cases[key],
            all(b.with_skill.passed for b in baselines),
            all(b.without_skill.passed for b in baselines),
        )
        for key, baselines in by_case.items()
    ]


@dataclass(frozen=True)
class LoadingTally:
    """`skill_loading` prompts of one kind (should load, or should not) and their runs."""

    prompts: int = 0
    prompts_passed: int = 0  # by the same rule as any case: `min_passes` of the runs
    runs: int = 0
    runs_passed: int = 0

    def add(self, result: CaseResult) -> LoadingTally:
        return LoadingTally(
            self.prompts + 1,
            self.prompts_passed + result.passed,
            self.runs + len(result.runs),
            self.runs_passed + result.passes,
        )


@dataclass(frozen=True)
class SkillLoadingSummary:
    """A skill's `skill_loading` prompts: those that should load it (does it load when it
    should?) and those that should not (does it stay out when it should?)."""

    plugin: str
    skill: str
    should_load: LoadingTally
    should_not_load: LoadingTally

    @property
    def key(self) -> str:
        return f"{self.plugin}/{self.skill}"


def skill_loading_summaries(results: list[CaseResult]) -> list[SkillLoadingSummary]:
    """Per skill, in the order of `results`, its `skill_loading` prompts that passed.
    Skills without any are left out."""
    summaries: dict[str, SkillLoadingSummary] = {}
    for result in results:
        case = result.case
        if result.metric_name != "skill_loading" or not case.is_skill_loading:
            continue
        key = f"{case.plugin}/{case.skill}"
        summary = summaries.get(key) or SkillLoadingSummary(case.plugin, case.skill, LoadingTally(), LoadingTally())
        if case.should_load:
            summary = replace(summary, should_load=summary.should_load.add(result))
        else:
            summary = replace(summary, should_not_load=summary.should_not_load.add(result))
        summaries[key] = summary
    return list(summaries.values())


def _run_once(runner: Runner, task: AgentTask, prefix: str) -> AgentRun:
    with tempfile.TemporaryDirectory(prefix=prefix) as root:
        try:
            trace = runner.run(task, Path(root))
        except Exception as e:  # a broken run counts as a failure, it does not abort the rest
            return AgentRun(None, f"{type(e).__name__}: {e}")
    # A run on a model other than the pinned one is not comparable: it does not count.
    for agent, models in trace.models.items():
        if error := model_mismatch(models, runner.model):
            return AgentRun(trace, f"{agent}: {error} (log={trace.raw_log})")
    return AgentRun(trace)
