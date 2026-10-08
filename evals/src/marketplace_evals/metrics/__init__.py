"""The metrics, which golden cases each one scores, and how many runs must pass it.

- expected_calls / forbidden_calls: deterministic, on the calls in the trace (calls.py).
- rules_always_met / outcome_checks: deterministic, the golden's commands on the final state (checks.py).
- rules_always_met_judged / outcome: an LLM judge, criterion by criterion (criteria.py).
- skill_loading: deterministic, whether the agent loads the skill, on the golden's
  `skill_loading` prompts only (skill_loading.py).
- integrity: deterministic, whether the agent tried to read the eval or leave its
  sandbox, on every case (integrity.py).

The prohibitions (forbidden_calls, rules_always_met, rules_always_met_judged and integrity) are
strict: every run must pass them (pass^k), since a user needs them kept every time.
The rest pass a case with `min_passes` of the runs, and are informative in a case marked
`informative`: scored and reported, but they never fail the session.
"""

from collections.abc import Callable
from dataclasses import dataclass

from marketplace_evals.goldens import GoldenCase
from marketplace_evals.metrics.base import Check, MetricResult
from marketplace_evals.metrics.calls import expected_calls, forbidden_calls
from marketplace_evals.metrics.checks import outcome_checks, rules_always_met
from marketplace_evals.metrics.criteria import outcome, rules_always_met_judged
from marketplace_evals.metrics.integrity import integrity
from marketplace_evals.metrics.skill_loading import skill_loading
from marketplace_evals.runtimes.judge import Judge
from marketplace_evals.trace import Trace

# (golden case, trace) -> result for one run
Metric = Callable[[GoldenCase, Trace], MetricResult]


@dataclass(frozen=True)
class MetricSpec:
    measure: Callable[[GoldenCase, Trace, Judge], MetricResult]
    applies_to: Callable[[GoldenCase], bool]  # whether a case has anything for it to score
    strict: bool = False  # a prohibition: every run must pass, not just `min_passes`
    # Scores the trace's calls: for the baseline, what only the skill can pass is left out.
    on_calls: bool = False

    def informative(self, case: GoldenCase) -> bool:
        """Whether failing it on `case` is only reported: a quality metric of an
        informative case. A prohibition never is."""
        return case.informative and not self.strict

    def bind(self, judge: Judge) -> Metric:
        return lambda case, trace: self.measure(case, trace, judge)


METRICS: dict[str, MetricSpec] = {
    "expected_calls": MetricSpec(
        lambda case, trace, _: expected_calls(case, trace), lambda case: bool(case.expected_calls), on_calls=True
    ),
    "forbidden_calls": MetricSpec(
        lambda case, trace, _: forbidden_calls(case, trace),
        lambda case: bool(case.forbidden_calls),
        strict=True,
        on_calls=True,
    ),
    "rules_always_met": MetricSpec(
        lambda case, trace, _: rules_always_met(case, trace), lambda case: bool(case.rules_always_met), strict=True
    ),
    "rules_always_met_judged": MetricSpec(
        rules_always_met_judged, lambda case: bool(case.rules_always_met_judged), strict=True
    ),
    "outcome_checks": MetricSpec(
        lambda case, trace, _: outcome_checks(case, trace), lambda case: bool(case.outcome_checks)
    ),
    "outcome": MetricSpec(outcome, lambda case: bool(case.expected_outcome)),
    "skill_loading": MetricSpec(lambda case, trace, _: skill_loading(case, trace), lambda case: case.is_skill_loading),
    "integrity": MetricSpec(lambda case, trace, _: integrity(case, trace), lambda case: True, strict=True),
}

__all__ = [
    "METRICS",
    "Check",
    "Metric",
    "MetricResult",
    "MetricSpec",
    "expected_calls",
    "forbidden_calls",
    "integrity",
    "outcome",
    "outcome_checks",
    "rules_always_met",
    "rules_always_met_judged",
    "skill_loading",
]
