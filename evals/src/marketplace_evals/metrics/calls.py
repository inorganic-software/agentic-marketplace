"""The calls the agent made: the ones it had to make, and the ones it must never make.

Inspired by DeepEval's ToolCorrectnessMetric, which compares the tools called with
the expected ones. They are two metrics because they pass differently: an expected
call is a quality criterion (`min_passes` of the runs), a forbidden call a prohibition
(every run). Both work on canonical actions so as not to depend on the runtime.
"""

from marketplace_evals.goldens import GoldenCase
from marketplace_evals.matchers import CallMatcher
from marketplace_evals.metrics.base import Check, MetricResult
from marketplace_evals.trace import Trace

# Offending calls shown in a failed check; the rest are counted.
SHOWN_OFFENDING_CALLS = 3


def expected_calls(case: GoldenCase, trace: Trace) -> MetricResult:
    """Every expected call appears, in any order."""
    checks = [
        Check(f"calls {m}", any(m.matches(c) for c in trace.calls), key=f"calls {m}") for m in case.expected_calls
    ]
    return _all_pass("expected_calls", checks)


def forbidden_calls(case: GoldenCase, trace: Trace) -> MetricResult:
    """No forbidden call appears."""
    return _all_pass("forbidden_calls", [_forbidden(m, trace) for m in case.forbidden_calls])


def _all_pass(name: str, checks: list[Check]) -> MetricResult:
    score = sum(c.passed for c in checks) / len(checks) if checks else 1.0
    return MetricResult(name, score, 1.0, checks)


def _forbidden(matcher: CallMatcher, trace: Trace) -> Check:
    offending = [c for c in trace.calls if matcher.matches(c)]
    detail = "; ".join(str(c) for c in offending[:SHOWN_OFFENDING_CALLS])
    if len(offending) > SHOWN_OFFENDING_CALLS:
        detail += f"; +{len(offending) - SHOWN_OFFENDING_CALLS} more"
    description = f"does not call {matcher}" + (f" — got {detail}" if offending else "")
    return Check(description, not offending, key=f"does not call {matcher}")
