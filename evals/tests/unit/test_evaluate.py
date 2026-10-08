from dataclasses import replace
from pathlib import Path

from marketplace_evals.config import EvalConfig
from marketplace_evals.evaluation import (
    AgentRun,
    Baseline,
    CaseResult,
    LoadingTally,
    case_baselines,
    run_agent,
    score_baseline,
    score_runs,
    skill_loading_summaries,
)
from marketplace_evals.goldens import GoldenCase, PromptVariant
from marketplace_evals.matchers import CallMatcher
from marketplace_evals.metrics import METRICS, Check, MetricResult, expected_calls
from marketplace_evals.reporting.terminal import (
    format_baselines,
    format_case_result,
    format_delta,
    format_skill_loading,
    format_status,
)
from marketplace_evals.runtimes import AgentTask, Runner
from marketplace_evals.sandbox import Sandbox
from marketplace_evals.session import EvalSession
from marketplace_evals.trace import Trace
from marketplace_evals.usage import Usage

CASE = GoldenCase("c", "plugin", "skill", Path("f"), (PromptVariant("v", "p"),), [], [])


class FakeRunner(Runner):
    name = "fake"

    def __init__(self, model: str, used: str):
        self.model, self.used = model, used

    def _launch(self, task: AgentTask, sandbox: Sandbox) -> Trace:
        raise NotImplementedError

    def run(self, task: AgentTask, root: Path) -> Trace:
        return Trace(
            runtime="fake",
            models={"main": {self.used}},
            usage=Usage(calls=1),
            raw_log=f"{root.name}|{task.without_skill}",
        )


def test_runs_on_another_model_are_errors_but_keep_their_usage():
    runs = run_agent(CASE, FakeRunner("claude-sonnet-5-5", "claude-sonnet-5"), Path("."), 2)

    assert all("model mismatch" in r.error for r in runs)
    assert all(r.trace.usage.calls == 1 for r in runs)

    result = score_runs(CASE, "expected_calls", expected_calls, runs, 1)
    assert result.passes == 0
    assert all(r.metric is None for r in result.runs)


def test_runs_on_the_pinned_model_are_scored():
    runs = run_agent(CASE, FakeRunner("claude-sonnet-5-5", "claude-sonnet-5-5"), Path("."), 2)
    assert all(r.error is None for r in runs)
    assert score_runs(CASE, "expected_calls", expected_calls, runs, 2).passed


def test_case_reports_name_the_plugin_and_the_skill():
    runs = run_agent(CASE, FakeRunner("m", "m"), Path("."), 1)
    report = format_case_result(score_runs(CASE, "expected_calls", expected_calls, runs, 1))
    assert report.startswith("plugin/skill/c · expected_calls: 1/1 runs OK (minimum 1)")


def violated_in(*bad: int):
    """A metric that fails the runs whose trace has a raw_log in `bad`."""

    def metric(case, trace):
        passed = trace.raw_log not in bad
        return MetricResult("m", float(passed), 1.0, [Check("rule", passed)])

    return metric


def three_runs(error_in: int | None = None) -> list[AgentRun]:
    return [AgentRun(Trace(runtime="t", raw_log=i), "boom" if i == error_in else None) for i in range(3)]


def test_a_strict_metric_fails_the_case_if_a_single_run_breaks_it():
    # 2 of 3 is enough for a quality criterion, not for a prohibition.
    assert score_runs(CASE, "m", violated_in(1), three_runs(), 2).passed
    strict = score_runs(CASE, "m", violated_in(1), three_runs(), 2, strict=True)
    assert (strict.passes, strict.required, strict.passed) == (2, 3, False)
    assert score_runs(CASE, "m", violated_in(), three_runs(), 2, strict=True).passed


def test_a_run_with_an_error_fails_a_strict_metric():
    result = score_runs(CASE, "m", violated_in(), three_runs(error_in=0), 2, strict=True)
    assert (result.passes, result.passed) == (2, False)


def test_a_strict_report_says_every_run_must_pass():
    report = format_case_result(score_runs(CASE, "m", violated_in(), three_runs(), 2, strict=True))
    assert report.startswith("plugin/skill/c · m: 3/3 runs OK (strict: all 3)")


def test_in_an_informative_case_only_the_metrics_that_are_not_strict_are_informative():
    informative = replace(CASE, informative=True)
    assert METRICS["outcome_checks"].informative(informative)
    assert not METRICS["rules_always_met"].informative(informative)
    assert not METRICS["outcome_checks"].informative(CASE)

    quality = score_runs(informative, "m", violated_in(0, 1), three_runs(), 2)
    prohibition = score_runs(informative, "m", violated_in(0, 1), three_runs(), 2, strict=True)
    assert (quality.passed, quality.informative) == (False, True)
    assert (prohibition.passed, prohibition.informative) == (False, False)


def test_an_informative_report_says_so_and_its_status_is_xfail_or_xpass():
    case = replace(CASE, informative=True)
    failed = score_runs(case, "m", violated_in(0, 1), three_runs(), 2)
    passed = score_runs(case, "m", violated_in(), three_runs(), 2)
    assert format_case_result(failed).startswith("plugin/skill/c · m: 1/3 runs OK (minimum 2, informative)")
    assert (format_status(failed), format_status(passed)) == ("XFAIL", "XPASS")
    assert format_status(score_runs(CASE, "m", violated_in(0, 1), three_runs(), 2)) == "FAILED"


def test_baseline_runs_leave_out_the_skill_and_are_named_apart():
    [run] = run_agent(CASE, FakeRunner("m", "m"), Path("."), 1, without_skill=True)
    folder, without_skill = run.trace.raw_log.split("|")
    assert folder.startswith("plugin-skill-c-baseline-0-") and without_skill == "skill"
    [run] = run_agent(CASE, FakeRunner("m", "m"), Path("."), 1)
    folder, without_skill = run.trace.raw_log.split("|")
    assert folder.startswith("plugin-skill-c-0-") and without_skill == "None"


LOADS_SKILL = CallMatcher("load_skill", {"name": "skill"})
FETCHES = CallMatcher("shell", {"command": "*fetch*"})


def calls_metric(scored: list):
    """`expected_calls`, recording the cases it scored: whatever the trace, each run
    passes if its raw_log is "pass"."""

    def metric(case, trace):
        scored.append(case)
        passed = trace.raw_log == "pass"
        return MetricResult("m", float(passed), 1.0, [Check(str(e), passed) for e in case.expected_calls])

    return metric


def runs_of(*logs: str) -> list[AgentRun]:
    return [AgentRun(Trace(runtime="t", raw_log=log)) for log in logs]


def test_the_delta_is_the_difference_of_pass_rates():
    case = replace(CASE, expected_calls=[FETCHES])
    scored = []
    metric = calls_metric(scored)
    with_skill = runs_of("pass", "pass", "pass")
    result = score_runs(case, "expected_calls", metric, with_skill, 2)
    baseline = score_baseline(result, metric, with_skill, runs_of("pass", "fail", "fail"), 2, on_calls=True)

    assert baseline.with_skill is result  # nothing to leave out: not scored again
    assert len(scored) == 6
    assert (baseline.without_skill.passes, baseline.without_skill.passed) == (1, False)
    assert round(baseline.delta, 2) == 0.67
    assert format_delta(baseline.delta) == "+67 pp" and format_delta(0) == "0 pp" and format_delta(-1 / 3) == "-33 pp"


def test_both_groups_are_scored_without_what_only_the_skill_can_pass():
    case = replace(CASE, expected_calls=[LOADS_SKILL, FETCHES])
    scored = []
    metric = calls_metric(scored)
    with_skill = runs_of("pass")
    result = score_runs(case, "expected_calls", metric, with_skill, 1)
    baseline = score_baseline(result, metric, with_skill, runs_of("pass"), 1, on_calls=True)

    assert baseline.with_skill is not result
    assert [c.expected_calls for c in scored[1:]] == [[FETCHES], [FETCHES]]
    assert baseline.delta == 0


def test_a_metric_not_on_calls_keeps_its_result_and_scores_the_whole_case_without_the_skill():
    case = replace(CASE, expected_calls=[LOADS_SKILL])  # the outcome is judged with it there
    scored = []
    metric = calls_metric(scored)
    with_skill = runs_of("pass")
    result = score_runs(case, "outcome", metric, with_skill, 1)
    baseline = score_baseline(result, metric, with_skill, runs_of("fail"), 1)

    assert baseline.with_skill is result and len(scored) == 2  # not judged twice
    assert scored[1] == case and baseline.delta == 1


def test_a_metric_only_the_skill_can_pass_has_no_baseline():
    case = replace(CASE, expected_calls=[LOADS_SKILL])
    scored = []
    metric = calls_metric(scored)
    result = score_runs(case, "expected_calls", metric, runs_of("pass"), 1)
    baseline = score_baseline(
        result, metric, runs_of("pass"), runs_of("fail"), 1, on_calls=True, applies_to=lambda c: bool(c.expected_calls)
    )

    assert (baseline.comparable, baseline.delta, len(scored)) == (False, None, 1)
    report = format_case_result(replace(result, baseline=baseline))
    assert "baseline: – (only a run with the skill can pass it)" in report


def result_with(passes_with: int, passes_without: int, case: GoldenCase = CASE) -> CaseResult:
    def scored(passes: int) -> CaseResult:
        runs = runs_of(*["pass"] * passes, *["fail"] * (3 - passes))
        return score_runs(case, "m", calls_metric([]), runs, 2)

    return replace(scored(passes_with), baseline=Baseline(scored(passes_with), scored(passes_without)))


def test_a_report_with_a_baseline_shows_both_groups_and_the_runs_without_the_skill():
    report = format_case_result(result_with(3, 1))
    assert "baseline: with skill 3/3 · without skill 1/3 · Δ +67 pp" in report
    assert report.count("  #1 ") == 2 and "  without skill:\n" in report


def test_a_case_passes_without_the_skill_only_if_every_comparable_metric_does():
    other = replace(CASE, id="d")
    results = [
        result_with(3, 3),
        result_with(3, 0),
        replace(result_with(3, 3), baseline=Baseline(None, None)),  # only the skill can pass it
        result_with(2, 2, other),
        score_runs(other, "m", calls_metric([]), runs_of("pass"), 1),  # a session without baseline
    ]
    baselines = case_baselines(results)

    assert [(b.case.key, b.passed_with, b.passed_without) for b in baselines] == [
        ("plugin/skill/c", True, False),
        ("plugin/skill/d", True, True),
    ]
    table = format_baselines(baselines).splitlines()
    assert "⚠" not in table[1] and "⚠ passes without the skill: it does not measure it" in table[2]


def loading_result(should_load: bool, passes: int, case_id: str = "c", skill: str = "skill") -> CaseResult:
    case = replace(CASE, id=case_id, skill=skill, should_load=should_load)
    runs = runs_of(*["pass"] * passes, *["fail"] * (3 - passes))
    return score_runs(case, "skill_loading", calls_metric([]), runs, 2)


def test_skill_loading_prompts_are_summed_up_per_skill_by_kind():
    results = [
        loading_result(True, 3, "a"),
        loading_result(True, 1, "b"),
        loading_result(False, 2, "c"),
        loading_result(True, 3, "a", skill="other"),
        result_with(3, 3),  # a case, not a prompt
    ]
    [skill, other] = skill_loading_summaries(results)

    assert skill.key == "plugin/skill"
    assert skill.should_load == LoadingTally(prompts=2, prompts_passed=1, runs=6, runs_passed=4)
    assert skill.should_not_load == LoadingTally(prompts=1, prompts_passed=1, runs=3, runs_passed=2)
    assert other.should_not_load == LoadingTally()

    table = format_skill_loading([skill, other]).splitlines()
    assert table[0].split() == ["skill", "should", "load", "should", "not", "load"]
    assert table[1].split() == ["plugin/skill", "1/2", "(4/6", "runs)", "1/1", "(2/3", "runs)"]
    assert table[2].split() == ["plugin/other", "1/1", "(3/3", "runs)", "–"]


def test_without_skill_loading_prompts_there_is_no_summary():
    assert skill_loading_summaries([result_with(3, 3)]) == []


class NoJudge:
    usage = Usage()


def test_a_baseline_session_does_not_run_skill_loading_prompts_without_the_skill():
    config = EvalConfig(
        provider="claude", backend="default", model="m", judge_model="j", runs=1, min_passes=1, baseline=True
    )
    session = EvalSession(config, runner=FakeRunner("m", "m"), judge=NoJudge())
    prompt = replace(CASE, id="should-load-x", should_load=True)

    session.runs(CASE)
    session.runs(prompt)
    assert list(session.baseline_runs_by_case) == [CASE.key]

    result = session.score(prompt, "skill_loading", METRICS["skill_loading"])
    assert result.baseline is None


VARIANTS = replace(
    CASE, prompts=(PromptVariant("a", "pide A"), PromptVariant("b", "pide B"), PromptVariant("c", "pide C"))
)


class PromptRunner(FakeRunner):
    """Leaves in raw_log the run's folder and the prompt it was given."""

    def run(self, task: AgentTask, root: Path) -> Trace:
        return Trace(
            runtime="fake", prompt=task.prompt, models={"main": {self.used}}, raw_log=f"{root.name}|{task.prompt}"
        )


def test_run_i_gets_variant_i_mod_n_with_and_without_the_skill():
    for without_skill in (False, True):
        runs = run_agent(VARIANTS, PromptRunner("m", "m"), Path("."), 4, without_skill=without_skill)
        assert [r.variant for r in runs] == ["a", "b", "c", "a"]
        assert [r.trace.prompt for r in runs] == ["pide A", "pide B", "pide C", "pide A"]


def test_a_run_of_a_case_with_variants_is_named_after_its_variant():
    runs = run_agent(VARIANTS, PromptRunner("m", "m"), Path("."), 2, without_skill=True)
    assert runs[1].trace.raw_log.split("|")[0].startswith("plugin-skill-c-baseline-b-1-")
    [single] = run_agent(CASE, PromptRunner("m", "m"), Path("."), 1)
    assert single.variant == "v" and single.trace.raw_log.split("|")[0].startswith("plugin-skill-c-0-")


def variant_runs(*logs: str) -> list[AgentRun]:
    """Runs whose raw_log says whether they pass, with the variants in order."""
    return [AgentRun(Trace(runtime="t", raw_log=log), variant=VARIANTS.variant(i).id) for i, log in enumerate(logs)]


def test_runs_that_passed_are_tallied_per_variant_and_one_no_run_got_has_none():
    result = score_runs(VARIANTS, "m", violated_in("fail"), variant_runs("pass", "fail"), 1)
    assert {v: (t.passes, t.runs) for v, t in result.by_variant().items()} == {"a": (1, 1), "b": (0, 1), "c": (0, 0)}
    assert result.passed  # the case passes on all its runs together
    report = format_case_result(result)
    assert "  #1 [a] score=" in report and "  #2 [b] score=" in report
    assert "  by variant: a 1/1 · b 0/1 · c not run" in report


def test_a_case_with_one_variant_is_not_broken_down():
    report = format_case_result(score_runs(CASE, "m", violated_in(), runs_of("x"), 1))
    assert "by variant" not in report and "[v]" not in report


def test_with_a_baseline_each_variant_has_its_delta():
    metric = violated_in("fail")
    with_skill = variant_runs("pass", "pass", "pass")
    result = score_runs(VARIANTS, "outcome", metric, with_skill, 2)
    baseline = score_baseline(result, metric, with_skill, variant_runs("pass", "fail", "fail"), 2)
    assert baseline.variant_deltas() == {"a": 0, "b": 1, "c": 1}
    report = format_case_result(replace(result, baseline=baseline))
    assert "  baseline by variant: a 1/1 vs 1/1 (0 pp) · b 1/1 vs 0/1 (+100 pp) · c 1/1 vs 0/1 (+100 pp)" in report
    short = score_baseline(result, metric, with_skill, variant_runs("pass"), 2)
    assert short.variant_deltas() == {"a": 0, "b": None, "c": None}
