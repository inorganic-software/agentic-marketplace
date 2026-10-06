"""Plain-text reports for the terminal and summary.txt."""

from marketplace_evals.evaluation import AgentRun, CaseResult
from marketplace_evals.trace import MAIN_AGENT
from marketplace_evals.usage import Usage, total

MIN_CASE_COLUMN = 32

# (title, Usage attribute, note) of the usage columns that only some runtimes report.
PRICED_COLUMNS = (
    ("cost $", "cost_usd", "cost = list-price estimate"),
    ("AI cred", "ai_credits", "AI cred = Copilot"),
)


def format_case_result(result: CaseResult) -> str:
    """A metric over every run of a case: each run's score, checks and the judge's reasons."""
    lines = [
        f"{result.case.key} · {result.metric_name}: {result.passes}/{len(result.runs)} "
        f"runs OK (minimum {result.min_passes})"
    ]
    for i, r in enumerate(result.runs, 1):
        if r.run.error:
            lines.append(f"  #{i} AGENT ERROR: {r.run.error}")
            continue
        metric, trace = r.metric, r.run.trace
        if metric.error:
            lines.append(f"  #{i} {metric.error} log={trace.raw_log}")
            continue
        models = {agent: sorted(ms) for agent, ms in trace.models.items()}
        lines.append(
            f"  #{i} score={metric.score:.2f} (threshold {metric.threshold}) "
            f"turns={trace.turns} tool_calls={trace.tool_calls()} models={models} log={trace.raw_log}"
        )
        lines += [f"     [{'x' if c.passed else ' '}] {c.description}" for c in metric.checks]
        if metric.reason:
            lines.append(f"     judge: {metric.reason}")
    return "\n".join(lines)


def format_turns(runs_by_case: dict[str, list[AgentRun]]) -> str:
    """Turns (model replies) and tool calls per agent, for each run of each case (by its key)."""
    agents = sorted(
        {agent for runs in runs_by_case.values() for r in runs if r.trace for agent in r.trace.turns},
        key=lambda agent: (agent != MAIN_AGENT, agent),
    )
    labels = [f"{agent} turns/tools" for agent in agents]
    widths = [len(label) + 2 for label in labels]
    case_width = max([MIN_CASE_COLUMN] + [len(key) + 2 for key in runs_by_case])
    header = f"{'case':<{case_width}}{'run':>4}" + "".join(f"{lb:>{w}}" for lb, w in zip(labels, widths, strict=True))
    lines = [header]
    for key, runs in runs_by_case.items():
        for i, r in enumerate(runs, 1):
            if r.trace is None:
                lines.append(f"{key:<{case_width}}{i:>4}  (no trace: {r.error})")
                continue
            tools = r.trace.tool_calls()
            cells = "".join(
                f"{f'{r.trace.turns.get(agent, 0)} / {tools.get(agent, 0)}':>{w}}"
                for agent, w in zip(agents, widths, strict=True)
            )
            lines.append(f"{key:<{case_width}}{i:>4}{cells}")
    return "\n".join(lines)


def format_usage(agent: Usage, judge: Usage, wall_clock_s: float) -> str:
    """Tokens, cost and time of the agent, the judge and both."""
    rows = [("agent", agent), ("judge", judge), ("total", total([agent, judge]))]
    # Cost and AI credits only when the runtime reports them: a column of zeros would
    # read as free.
    priced = [col for col in PRICED_COLUMNS if any(getattr(u, col[1]) for _, u in rows)]
    header = (
        f"{'':<7}{'calls':>6}{'input':>10}{'output':>10}{'reasoning':>11}{'cache rd':>11}"
        f"{'cache wr':>11}{'total tok':>12}" + "".join(f"{title:>9}" for title, _, _ in priced) + f"{'time':>9}  models"
    )
    lines = [header]
    for name, u in rows:
        lines.append(
            f"{name:<7}{u.calls:>6}{u.input_tokens:>10,}{u.output_tokens:>10,}{u.reasoning_tokens:>11,}"
            f"{u.cache_read_tokens:>11,}{u.cache_write_tokens:>11,}{u.total_tokens:>12,}"
            + "".join(f"{getattr(u, attr):>9.2f}" for _, attr, _ in priced)
            + f"{format_duration(u.duration_s):>9}  {', '.join(sorted(u.models))}"
        )
    notes = ["time = sum of each call's duration (calls run in parallel)"] + [note for _, _, note in priced]
    lines.append(f"wall clock: {format_duration(wall_clock_s)} · " + " · ".join(notes))
    return "\n".join(lines)


def format_duration(seconds: float) -> str:
    """`2m05s`, or `42s` under a minute."""
    minutes, secs = divmod(round(seconds), 60)
    return f"{minutes}m{secs:02d}s" if minutes else f"{secs}s"
