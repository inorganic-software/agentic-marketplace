"""What each case's runs spend: turns, tool calls, tokens, cost and time.

Nothing new is collected: every run's trace already has its turns, tool calls and usage.
Each case gets the median of its runs, over those without an error only (another model,
a runtime that failed): they are not comparable, as they do not count for the metrics
either. Their spending is still in the session's total. With a baseline, the runs
without the skill get their own median, and each measure the difference (with the skill
minus without it). It is informative: nothing passes or fails on it.
"""

from dataclasses import dataclass
from statistics import median

from marketplace_evals.evaluation import AgentRun
from marketplace_evals.goldens import GoldenCase

# In the order they are reported. Turns and tool calls add up the main agent and its
# subagents. Cost is in dollars with Claude and in AI credits with Copilot.
MEASURES = ("turns", "tool_calls", "tokens", "cost_usd", "ai_credits", "duration_s")


def run_measures(run: AgentRun) -> dict[str, float | None]:
    """A run's measures. A cost the runtime does not report is None, not 0: zero would
    read as free."""
    trace = run.trace
    if trace is None:
        return dict.fromkeys(MEASURES)
    return {
        "turns": sum(trace.turns.values()),
        "tool_calls": sum(trace.tool_calls().values()),
        "tokens": trace.usage.total_tokens,
        "cost_usd": trace.usage.cost_usd or None,
        "ai_credits": trace.usage.ai_credits or None,
        "duration_s": trace.usage.duration_s,
    }


@dataclass(frozen=True)
class Efficiency:
    """The runs of one group (with the skill, or without it) and their median per measure."""

    runs: list[AgentRun]

    @property
    def valid(self) -> list[AgentRun]:
        """The runs that count: without an error."""
        return [r for r in self.runs if r.error is None and r.trace is not None]

    def median(self) -> dict[str, float | None]:
        """Per measure, the median of the valid runs that report it; None if none does.
        With an even number of runs, the mean of the two in the middle."""
        values = [run_measures(r) for r in self.valid]
        result: dict[str, float | None] = {}
        for measure in MEASURES:
            reported = [v[measure] for v in values if v[measure] is not None]
            result[measure] = median(reported) if reported else None
        return result


@dataclass(frozen=True)
class Delta:
    absolute: float
    percent: float | None  # of the value without the skill; None if that is 0


@dataclass(frozen=True)
class CaseEfficiency:
    case: GoldenCase
    with_skill: Efficiency
    without_skill: Efficiency | None = None  # with a baseline

    def delta(self) -> dict[str, Delta | None]:
        """Per measure, the median with the skill minus without it; None where either has
        no value. Empty without a baseline."""
        if self.without_skill is None:
            return {}
        with_skill, without_skill = self.with_skill.median(), self.without_skill.median()
        deltas: dict[str, Delta | None] = {}
        for measure in MEASURES:
            w, wo = with_skill[measure], without_skill[measure]
            deltas[measure] = None if w is None or wo is None else Delta(w - wo, (w - wo) / wo * 100 if wo else None)
        return deltas
