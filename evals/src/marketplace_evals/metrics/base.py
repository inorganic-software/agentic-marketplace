"""What every metric returns, and the judge's retry."""

from collections.abc import Callable
from dataclasses import dataclass, field

from marketplace_evals.runtimes.judge import JudgeError


@dataclass(frozen=True)
class Check:
    description: str  # for people: may include run-specific detail (judge reason, calls)
    passed: bool
    key: str | None = None  # stable across runs, to compare them; defaults to description

    @property
    def id(self) -> str:
        return self.key or self.description


@dataclass(frozen=True)
class MetricResult:
    name: str
    score: float  # 0..1
    threshold: float
    checks: list[Check] = field(default_factory=list)
    reason: str | None = None  # the judge's reasoning, if any
    error: str | None = None  # could not be scored (e.g. the judge failed twice)

    @property
    def passed(self) -> bool:
        return self.error is None and self.score >= self.threshold


def failed(name: str, threshold: float, error: str) -> MetricResult:
    return MetricResult(name, 0.0, threshold, error=error)


def with_judge_retry(name: str, threshold: float, fn: Callable[[], MetricResult]) -> MetricResult:
    """Retry once if the judge fails; if it fails again, the run does not pass."""
    try:
        return fn()
    except JudgeError:
        try:
            return fn()
        except JudgeError as e:
            return failed(name, threshold, f"JUDGE ERROR: {e}")
