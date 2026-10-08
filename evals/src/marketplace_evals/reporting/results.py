"""Structured results of an eval session, saved as `.runs/<date>/results.json`.

The point is to compare sessions over time (a skill change, a new model): for each
case, metric and check it records how many of the runs passed, under a stable key.
It also records what the session ran with, so a difference can be traced to its
cause. Written once per session, at the end.
"""

import subprocess
from collections.abc import Sequence
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from marketplace_evals.config import EvalConfig
from marketplace_evals.efficiency import CaseEfficiency, Efficiency, run_measures
from marketplace_evals.evaluation import Baseline, CaseResult, CaseStubGaps, case_baselines, skill_loading_summaries
from marketplace_evals.usage import Usage

# 2: each metric records the runs it required (`required`, `strict`) instead of `min_passes`.
# 3: with `config.baseline`, each metric and case records how it did without the skill.
# 4: `skill_loading` prompts record `should_load`, and `skill_loading` sums them up per skill.
# 5: each case records `informative`, and each metric whether failing it only is reported.
# 6: each check of `outcome` records whether it is `required`.
# 7: each run records its prompt `variant`; with several, each metric records `variants`
#    (runs that passed per variant) and its baseline the `delta` of each one.
# 8: each case records its `efficiency` (median turns, tool calls, tokens, cost and time,
#    with and without the skill), and `usage.agent` leaves out the runs without the skill,
#    which go to `usage.agent_without_skill`.
# 9: each run records its `stub_gaps` (calls the fixture's stubs do not imitate), and each
#    case how many of its runs had any (`runs_with_stub_gaps`, and with a baseline
#    `runs_with_stub_gaps_without_skill`).
SCHEMA_VERSION = 9


def results_document(
    case_results: list[CaseResult],
    config: EvalConfig,
    runtime_version: str | None,
    repo: Path,
    started_at: datetime,
    agent_usage: Usage,
    judge_usage: Usage,
    wall_clock_s: float,
    efficiencies: Sequence[CaseEfficiency] = (),
    agent_without_skill: Usage | None = None,
    stub_gaps: Sequence[CaseStubGaps] = (),
) -> dict:
    cases = _cases(case_results)
    for e in efficiencies:
        if e.case.key in cases:
            cases[e.case.key]["efficiency"] = _efficiency(e)
    for g in stub_gaps:
        if g.case.key in cases:
            cases[g.case.key]["runs_with_stub_gaps"] = g.runs_with_gaps
            if g.without_skill is not None:
                cases[g.case.key]["runs_with_stub_gaps_without_skill"] = g.runs_with_gaps_without_skill
    usage = {"agent": _usage(agent_usage)}
    if agent_without_skill is not None:
        usage["agent_without_skill"] = _usage(agent_without_skill)
    return {
        "schema_version": SCHEMA_VERSION,
        "started_at": started_at.isoformat(timespec="seconds"),
        "config": {
            "provider": config.provider,
            "backend": config.backend,
            "runtime": config.runtime,
            "runtime_version": runtime_version,
            "model": config.model,
            "judge": config.judge,
            "judge_model": config.judge_model,
            "runs": config.runs,
            "min_passes": config.min_passes,
            "baseline": config.baseline,
        },
        "repo": repo_version(repo),
        "usage": {
            **usage,
            "judge": _usage(judge_usage),
            "wall_clock_s": round(wall_clock_s, 1),
        },
        "cases": cases,
        "skill_loading": {
            s.key: {"should_load": asdict(s.should_load), "should_not_load": asdict(s.should_not_load)}
            for s in skill_loading_summaries(case_results)
        },
    }


def repo_version(repo: Path) -> dict:
    """Commit of the repo holding the plugins and the evals, and whether they had
    uncommitted changes (then the commit alone does not say what ran)."""

    def git(*args: str) -> str:
        return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True).stdout.strip()

    return {
        "commit": git("rev-parse", "HEAD") or None,
        "branch": git("rev-parse", "--abbrev-ref", "HEAD") or None,
        "dirty": bool(git("status", "--porcelain", "--", "plugins", "evals")),
    }


def _cases(case_results: list[CaseResult]) -> dict:
    cases: dict[str, dict] = {}
    for result in case_results:
        case = result.case
        entry = cases.setdefault(
            case.key,
            {"plugin": case.plugin, "skill": case.skill, "informative": case.informative, "metrics": {}},
        )
        if case.is_skill_loading:
            entry["should_load"] = case.should_load
        entry["metrics"][result.metric_name] = _metric(result)
        if result.baseline is not None:
            entry["metrics"][result.metric_name]["baseline"] = _baseline(result.baseline)
    for b in case_baselines(case_results):
        cases[b.case.key]["baseline"] = {"with_skill": b.passed_with, "without_skill": b.passed_without}
    return cases


def _baseline(baseline: Baseline) -> dict | None:
    """The metric with and without the skill, both without what only the skill can pass,
    and the difference of their pass rates. None if only the skill can pass it."""
    if not baseline.comparable:
        return None
    entry = {
        "with_skill": _metric(baseline.with_skill),
        "without_skill": _metric(baseline.without_skill),
        "delta": round(baseline.delta, 4),
    }
    if baseline.with_skill.case.has_variants:
        entry["variant_deltas"] = {
            variant: None if delta is None else round(delta, 4) for variant, delta in baseline.variant_deltas().items()
        }
    return entry


def _metric(result: CaseResult) -> dict:
    checks: dict[str, dict] = {}
    per_run = []
    for r in result.runs:
        metric, trace = r.metric, r.run.trace
        per_run.append({
            "variant": r.run.variant,
            "passed": r.passed,
            "score": None if metric is None or metric.error else round(metric.score, 4),
            "error": r.run.error or (metric.error if metric else None),
            "log": trace.raw_log if trace else None,
            "stub_gaps": [{"stub": g.stub, "argv": list(g.argv), "reason": g.reason} for g in r.run.stub_gaps],
        })  # fmt: skip
        if metric is None or metric.error:  # not scored: its checks do not count
            continue
        for check in metric.checks:
            entry = checks.setdefault(
                check.id,
                {"passed": 0, "scored": 0} | ({} if check.required is None else {"required": check.required}),
            )
            entry["passed"] += check.passed
            entry["scored"] += 1
    entry = {
        "passes": result.passes,
        "runs": len(result.runs),
        "required": result.required,
        "strict": result.strict,
        "informative": result.informative,
        "passed": result.passed,
        "checks": checks,
        "per_run": per_run,
    }
    if result.case.has_variants:  # in the golden's order; a variant no run got has 0 runs
        entry["variants"] = {variant: asdict(t) for variant, t in result.by_variant().items()}
    return entry


def _efficiency(efficiency: CaseEfficiency) -> dict:
    """The median of each measure with the skill and, with a baseline, without it and the
    difference; the runs that count for the median out of all, and each run's measures."""
    entry = {"with_skill": _group(efficiency.with_skill)}
    if efficiency.without_skill is not None:
        entry["without_skill"] = _group(efficiency.without_skill)
        entry["delta"] = {
            measure: None if d is None else {"absolute": _round(d.absolute), "percent": _round(d.percent, 1)}
            for measure, d in efficiency.delta().items()
        }
    return entry


def _group(group: Efficiency) -> dict:
    return {
        "runs": len(group.valid),
        "total_runs": len(group.runs),
        "median": {measure: _round(value) for measure, value in group.median().items()},
        "per_run": [
            {"variant": r.variant, "error": r.error} | {m: _round(v) for m, v in run_measures(r).items()}
            for r in group.runs
        ],
    }


def _round(value: float | None, digits: int = 4) -> float | None:
    return None if value is None else round(value, digits)


def _usage(usage: Usage) -> dict:
    data = asdict(usage)
    data["models"] = sorted(usage.models)
    data["cost_usd"] = round(usage.cost_usd, 4)
    data["ai_credits"] = round(usage.ai_credits, 4)  # 0 on Vertex, which reports no credits
    data["duration_s"] = round(usage.duration_s, 1)
    return data
