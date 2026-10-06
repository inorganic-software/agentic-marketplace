"""Structured results of an eval session, saved as `.runs/<date>/results.json`.

The point is to compare sessions over time (a skill change, a new model): for each
case, metric and check it records how many of the runs passed, under a stable key.
It also records what the session ran with, so a difference can be traced to its
cause. Written once per session, at the end.
"""

import subprocess
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from marketplace_evals.config import EvalConfig
from marketplace_evals.evaluation import CaseResult
from marketplace_evals.usage import Usage

SCHEMA_VERSION = 1


def results_document(
    case_results: list[CaseResult],
    config: EvalConfig,
    runtime_version: str | None,
    repo: Path,
    started_at: datetime,
    agent_usage: Usage,
    judge_usage: Usage,
    wall_clock_s: float,
) -> dict:
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
        },
        "repo": repo_version(repo),
        "usage": {
            "agent": _usage(agent_usage),
            "judge": _usage(judge_usage),
            "wall_clock_s": round(wall_clock_s, 1),
        },
        "cases": _cases(case_results),
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
        entry = cases.setdefault(case.key, {"plugin": case.plugin, "skill": case.skill, "metrics": {}})
        entry["metrics"][result.metric_name] = _metric(result)
    return cases


def _metric(result: CaseResult) -> dict:
    checks: dict[str, dict] = {}
    per_run = []
    for r in result.runs:
        metric, trace = r.metric, r.run.trace
        per_run.append({
            "passed": r.passed,
            "score": None if metric is None or metric.error else round(metric.score, 4),
            "error": r.run.error or (metric.error if metric else None),
            "log": trace.raw_log if trace else None,
        })  # fmt: skip
        if metric is None or metric.error:  # not scored: its checks do not count
            continue
        for check in metric.checks:
            entry = checks.setdefault(check.id, {"passed": 0, "scored": 0})
            entry["passed"] += check.passed
            entry["scored"] += 1
    return {
        "passes": result.passes,
        "runs": len(result.runs),
        "min_passes": result.min_passes,
        "passed": result.passed,
        "checks": checks,
        "per_run": per_run,
    }


def _usage(usage: Usage) -> dict:
    data = asdict(usage)
    data["models"] = sorted(usage.models)
    data["cost_usd"] = round(usage.cost_usd, 4)
    data["ai_credits"] = round(usage.ai_credits, 4)  # 0 on Vertex, which reports no credits
    data["duration_s"] = round(usage.duration_s, 1)
    return data
