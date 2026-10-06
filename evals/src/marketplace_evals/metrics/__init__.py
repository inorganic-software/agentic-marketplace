"""The metrics, and which golden cases each one scores.

- tool_correctness: deterministic, on the calls in the trace.
- rules / outcome: an LLM judge, criterion by criterion (criteria.py).
"""

from collections.abc import Callable
from dataclasses import dataclass

from marketplace_evals.goldens import GoldenCase
from marketplace_evals.metrics.base import Check, MetricResult
from marketplace_evals.metrics.criteria import outcome, rules
from marketplace_evals.metrics.tool_correctness import tool_correctness
from marketplace_evals.runtimes.judge import Judge
from marketplace_evals.trace import Trace

# (golden case, trace) -> result for one run
Metric = Callable[[GoldenCase, Trace], MetricResult]


@dataclass(frozen=True)
class MetricSpec:
    measure: Callable[[GoldenCase, Trace, Judge], MetricResult]
    applies_to: Callable[[GoldenCase], bool]  # whether a case has anything for it to score

    def bind(self, judge: Judge) -> Metric:
        return lambda case, trace: self.measure(case, trace, judge)


METRICS: dict[str, MetricSpec] = {
    "tool_correctness": MetricSpec(lambda case, trace, _: tool_correctness(case, trace), lambda case: True),
    "rules": MetricSpec(rules, lambda case: bool(case.rules)),
    "outcome": MetricSpec(outcome, lambda case: bool(case.expected_outcome)),
}

__all__ = ["METRICS", "Check", "Metric", "MetricResult", "MetricSpec", "outcome", "rules", "tool_correctness"]
