"""Tool correctness: did the agent use the right tools?

Inspired by DeepEval's ToolCorrectnessMetric, which compares the tools called with
the expected ones. Here we also check forbidden calls, and work on canonical actions
so as not to depend on the runtime.
"""

from marketplace_evals.goldens import GoldenCase
from marketplace_evals.matchers import CallMatcher
from marketplace_evals.metrics.base import Check, MetricResult
from marketplace_evals.trace import Trace

# Offending calls shown in a failed check; the rest are counted.
SHOWN_OFFENDING_CALLS = 3


def tool_correctness(case: GoldenCase, trace: Trace, threshold: float = 1.0) -> MetricResult:
    checks = [
        Check(f"calls {m}", any(m.matches(c) for c in trace.calls), key=f"calls {m}") for m in case.expected_calls
    ] + [_forbidden(m, trace) for m in case.forbidden_calls]
    score = sum(c.passed for c in checks) / len(checks) if checks else 1.0
    return MetricResult("tool_correctness", score, threshold, checks)


def _forbidden(matcher: CallMatcher, trace: Trace) -> Check:
    offending = [c for c in trace.calls if matcher.matches(c)]
    detail = "; ".join(str(c) for c in offending[:SHOWN_OFFENDING_CALLS])
    if len(offending) > SHOWN_OFFENDING_CALLS:
        detail += f"; +{len(offending) - SHOWN_OFFENDING_CALLS} more"
    description = f"does not call {matcher}" + (f" — got {detail}" if offending else "")
    return Check(description, not offending, key=f"does not call {matcher}")
