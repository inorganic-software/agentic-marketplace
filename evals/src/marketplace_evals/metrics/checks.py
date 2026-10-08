"""Deterministic checks: does the state the agent left pass the golden's commands?

Each check is a shell command run in the workspace after the agent (sandbox.py), with
the sandbox's environment, and passes if it exits with 0. Every check must pass. Two
metrics share it, as `rules_always_met_judged` and `outcome` share the judge:
  - rules_always_met: the golden's `rules_always_met`, the skill's absolute prohibitions,
    written as what must always be true, shared by every case. Run even if the agent
    changed nothing: in a trap that is the right behavior, and what was already there
    counts.
  - outcome_checks: the case's `outcome_checks`, what the skill asks for in that case.
    Fails without them if the agent changed nothing, as `outcome` does, so that a check
    such as "the working tree is clean" does not pass a run that did nothing.
"""

from marketplace_evals.goldens import GoldenCase, GoldenCheck
from marketplace_evals.metrics.base import Check, MetricResult, failed
from marketplace_evals.trace import Trace

THRESHOLD = 1.0


def rules_always_met(case: GoldenCase, trace: Trace) -> MetricResult:
    return run_checks("rules_always_met", case.rules_always_met, trace)


def outcome_checks(case: GoldenCase, trace: Trace) -> MetricResult:
    if not trace.changed:
        return failed("outcome_checks", THRESHOLD, "the agent changed no file and nothing its golden inspects")
    return run_checks("outcome_checks", case.outcome_checks, trace)


def run_checks(name: str, checks: tuple[GoldenCheck, ...], trace: Trace) -> MetricResult:
    results = [_check(c, trace) for c in checks]
    return MetricResult(name, sum(c.passed for c in results) / len(results), THRESHOLD, results)


def _check(check: GoldenCheck, trace: Trace) -> Check:
    run = trace.check_runs.get(check.name)
    if run is None:  # the runner did not run it: never a pass
        return Check(f"{check.name} — not run", False, key=check.name)
    if run.passed:
        return Check(check.name, True, key=check.name)
    detail = f"exit {run.exit_code}" + (f": {run.output}" if run.output else "")
    return Check(f"{check.name} — {detail}", False, key=check.name)
