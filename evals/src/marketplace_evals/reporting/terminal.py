"""Plain-text reports for the terminal and summary.txt."""

from marketplace_evals.efficiency import MEASURES, CaseEfficiency, Delta
from marketplace_evals.evaluation import (
    Baseline,
    CaseBaseline,
    CaseResult,
    CaseStubGaps,
    LoadingTally,
    SkillLoadingSummary,
    VariantTally,
)
from marketplace_evals.trace import StubGap
from marketplace_evals.usage import Usage, total

MIN_CASE_COLUMN = 32

# A case that passes without its skill: it does not tell whether the skill works.
NOT_MEASURED = "⚠ passes without the skill: it does not measure it"

# (title, Usage attribute, note) of the usage columns that only some runtimes report.
PRICED_COLUMNS = (
    ("cost $", "cost_usd", "cost = list-price estimate"),
    ("AI cred", "ai_credits", "AI cred = Copilot"),
)
PRICED_MEASURES = ("cost_usd", "ai_credits")

# (title, measure) of the efficiency table, in the order of MEASURES.
EFFICIENCY_COLUMNS = (
    ("turns", "turns"),
    ("tools", "tool_calls"),
    ("tokens", "tokens"),
    ("cost $", "cost_usd"),
    ("AI cred", "ai_credits"),
    ("time", "duration_s"),
)


def format_case_result(result: CaseResult) -> str:
    """A metric over every run of a case: each run's score, checks and the judge's reasons,
    and with several prompt variants, each run's variant and the runs that passed per
    variant. With a baseline, also the pass rates with and without the skill (per variant
    too), and the runs without it."""
    lines = [f"{result.case.key} · {result.metric_name}: {_passes(result)}", *_format_runs(result)]
    if result.case.has_variants:
        lines.append(f"  by variant: {format_variants(result.by_variant())}")
    if (baseline := result.baseline) is not None:
        if not baseline.comparable:
            lines.append("  baseline: – (only a run with the skill can pass it)")
        else:
            with_skill, without_skill = baseline.with_skill, baseline.without_skill
            lines.append(
                f"  baseline: with skill {with_skill.passes}/{len(with_skill.runs)} · "
                f"without skill {without_skill.passes}/{len(without_skill.runs)} · Δ {format_delta(baseline.delta)}"
                + ("" if with_skill is result else " (without the checks only the skill can pass)")
            )
            if result.case.has_variants:
                lines.append(f"  baseline by variant: {format_variant_deltas(baseline)}")
            lines.append("  without skill:")
            lines += _format_runs(without_skill)
    return "\n".join(lines)


def format_variants(tallies: dict[str, VariantTally]) -> str:
    """`directo 1/1 · coloquial 0/1 · indirecta not run`."""
    return " · ".join(
        f"{variant} {t.passes}/{t.runs}" if t.runs else f"{variant} not run" for variant, t in tallies.items()
    )


def format_variant_deltas(baseline: Baseline) -> str:
    """`directo 1/1 vs 0/1 (+100 pp) · coloquial 1/1 vs 1/1 (0 pp)`: with the skill, without
    it, and the difference, per variant."""
    with_skill, without_skill = baseline.with_skill.by_variant(), baseline.without_skill.by_variant()
    parts = []
    for variant, delta in baseline.variant_deltas().items():
        w, wo = with_skill[variant], without_skill[variant]
        parts.append(
            f"{variant} not run"
            if delta is None
            else f"{variant} {w.passes}/{w.runs} vs {wo.passes}/{wo.runs} ({format_delta(delta)})"
        )
    return " · ".join(parts)


def _passes(result: CaseResult) -> str:
    required = f"strict: all {result.required}" if result.strict else f"minimum {result.required}"
    if result.informative:
        required += ", informative"
    return f"{result.passes}/{len(result.runs)} runs OK ({required})"


def format_status(result: CaseResult) -> str:
    """As pytest reports it: PASSED or FAILED, and XPASS or XFAIL in an informative metric."""
    if result.informative:
        return "XPASS" if result.passed else "XFAIL"
    return "PASSED" if result.passed else "FAILED"


def _format_runs(result: CaseResult) -> list[str]:
    lines = []
    for i, r in enumerate(result.runs, 1):
        run = f"#{i} [{r.run.variant}]" if result.case.has_variants else f"#{i}"
        if r.run.error:
            lines.append(f"  {run} AGENT ERROR: {r.run.error}")
            lines += _format_stub_gaps(r.run.stub_gaps)
            continue
        metric, trace = r.metric, r.run.trace
        if metric.error:
            lines.append(f"  {run} {metric.error} log={trace.raw_log}")
            lines += _format_stub_gaps(r.run.stub_gaps)
            continue
        models = {agent: sorted(ms) for agent, ms in trace.models.items()}
        lines.append(
            f"  {run} score={metric.score:.2f} (threshold {metric.threshold}) "
            + ("required criterion missed " if metric.missed_required else "")
            + f"turns={trace.turns} tool_calls={trace.tool_calls()} models={models} log={trace.raw_log}"
        )
        lines += [f"     [{'x' if c.passed else ' '}] {c.description}" for c in metric.checks]
        if metric.reason:
            lines.append(f"     judge: {metric.reason}")
        lines += _format_stub_gaps(r.run.stub_gaps)
    return lines


def _format_stub_gaps(gaps: list[StubGap]) -> list[str]:
    """`stub gaps: gh pr create --fill: unknown flag: --fill · ...`, if the run made any."""
    return [f"     stub gaps: {' · '.join(str(g) for g in gaps)}"] if gaps else []


def format_delta(delta: float) -> str:
    """A difference of pass rates in percentage points: `+67 pp`, `0 pp`, `-33 pp`."""
    points = round(delta * 100)
    return f"{points:+d} pp" if points else "0 pp"


def format_baselines(baselines: list[CaseBaseline]) -> str:
    """Whether each case passes with its skill and without it. A case that also passes
    without it does not measure the skill."""
    if not baselines:
        return "no metric can be scored without the skill"
    case_width = max([MIN_CASE_COLUMN] + [len(b.case.key) + 2 for b in baselines])
    lines = [f"{'case':<{case_width}}{'with skill':>12}{'without skill':>15}"]
    for b in baselines:
        line = f"{b.case.key:<{case_width}}{_verdict(b.passed_with):>12}{_verdict(b.passed_without):>15}"
        lines.append(line + (f"  {NOT_MEASURED}" if b.passed_without else ""))
    lines.append("on the metrics that can be scored without the skill, leaving out what only the skill can pass")
    return "\n".join(lines)


def format_skill_loading(summaries: list[SkillLoadingSummary]) -> str:
    """Per skill, how many `skill_loading` prompts passed: those that should load it, and
    those that should not."""
    skill_width = max([MIN_CASE_COLUMN] + [len(s.key) + 2 for s in summaries])
    lines = [f"{'skill':<{skill_width}}{'should load':>20}{'should not load':>20}"]
    for s in summaries:
        lines.append(f"{s.key:<{skill_width}}{format_tally(s.should_load):>20}{format_tally(s.should_not_load):>20}")
    lines.append("prompts that passed (the same minimum of runs as any case), and runs that passed")
    return "\n".join(lines)


def format_stub_gaps(cases: list[CaseStubGaps]) -> str:
    """Per case with any, its runs that made a call the fixture's stubs do not imitate
    (with a baseline, also without the skill), and each distinct call."""
    rows = [c for c in cases if c.any]
    lines = []
    for c in rows:
        runs = f"{c.runs_with_gaps}/{len(c.with_skill)} runs"
        if c.without_skill is not None:
            runs += f" · without skill {c.runs_with_gaps_without_skill}/{len(c.without_skill)}"
        lines.append(f"{c.case.key}: {runs}")
        lines += [f"  {gap}" for gap in c.distinct()]
    lines.append(
        "calls the fixture's stubs answered with an error because they do not imitate them: "
        "the run may say more about the stub than about the agent"
    )
    return "\n".join(lines)


def format_tally(tally: LoadingTally) -> str:
    """`4/5 (13/15 runs)`, or `–` without prompts of that kind."""
    if not tally.prompts:
        return "–"
    return f"{tally.prompts_passed}/{tally.prompts} ({tally.runs_passed}/{tally.runs} runs)"


def _verdict(passed: bool) -> str:
    return "passed" if passed else "FAILED"


def format_efficiency(efficiencies: list[CaseEfficiency]) -> str:
    """Per case, the median of each measure over its runs without errors; with a baseline,
    also without the skill and the difference. The cost columns only when a run reports them."""
    rows: list[tuple[str, str, dict[str, str]]] = []  # (label, runs, measure -> text)
    priced: set[str] = set()
    for e in efficiencies:
        groups = [("", e.with_skill)] + ([("  without skill", e.without_skill)] if e.without_skill else [])
        for suffix, group in groups:
            median = group.median()
            priced |= {m for m in PRICED_MEASURES if median[m] is not None}
            label = suffix or e.case.key
            rows.append(
                (label, f"{len(group.valid)}/{len(group.runs)}", {m: format_measure(m, median[m]) for m in MEASURES})
            )
        if e.without_skill:
            rows.append(("  Δ", "", {m: format_measure_delta(m, d) for m, d in e.delta().items()}))
    columns = [(title, m) for title, m in EFFICIENCY_COLUMNS if m not in PRICED_MEASURES or m in priced]
    case_width = max([MIN_CASE_COLUMN] + [len(label) + 2 for label, _, _ in rows])
    widths = [max([len(title)] + [len(cells[m]) for _, _, cells in rows]) + 2 for title, m in columns]
    header = f"{'case':<{case_width}}{'runs':>6}" + "".join(
        f"{title:>{w}}" for (title, _), w in zip(columns, widths, strict=True)
    )
    lines = [header]
    for label, runs, cells in rows:
        lines.append(
            f"{label:<{case_width}}{runs:>6}"
            + "".join(f"{cells[m]:>{w}}" for (_, m), w in zip(columns, widths, strict=True))
        )
    lines.append(
        "median per run, over the runs without errors (runs: those out of all) · turns and tools add up "
        "the subagents · Δ = with the skill minus without it"
    )
    return "\n".join(lines)


def format_measure(measure: str, value: float | None) -> str:
    """`9`, `9.5`, `182,340`, `0.21` (cost), `48s` (time), or `–` without a value."""
    if value is None:
        return "–"
    if measure == "duration_s":
        return format_duration(value)
    if measure in PRICED_MEASURES:
        return f"{value:.2f}"
    return f"{value:,.0f}" if value == int(value) else f"{value:,.1f}"


def format_measure_delta(measure: str, delta: Delta | None) -> str:
    """`+3 (+50 %)`, `-1m05s (-20 %)`, `0 (0 %)`, or `–` where a side has no value."""
    if delta is None:
        return "–"
    sign = "+" if delta.absolute > 0 else "-" if delta.absolute < 0 else ""
    text = sign + format_measure(measure, abs(delta.absolute))
    if delta.percent is None:
        return text
    percent = round(delta.percent)
    return f"{text} ({percent:+d} %)" if percent else f"{text} (0 %)"


def format_usage(agent: Usage, judge: Usage, wall_clock_s: float, agent_without_skill: Usage | None = None) -> str:
    """Tokens, cost and time of the agent, the judge and all of them. With a baseline, the
    agent's runs without the skill get their own row."""
    agents = [("agent", agent)] + ([("agent without skill", agent_without_skill)] if agent_without_skill else [])
    rows = [*agents, ("judge", judge), ("total", total([u for _, u in agents] + [judge]))]
    # Cost and AI credits only when the runtime reports them: a column of zeros would
    # read as free.
    priced = [col for col in PRICED_COLUMNS if any(getattr(u, col[1]) for _, u in rows)]
    name_width = max(len(name) for name, _ in rows) + 1
    header = (
        f"{'':<{name_width}}{'calls':>6}{'input':>10}{'output':>10}{'reasoning':>11}{'cache rd':>11}"
        f"{'cache wr':>11}{'total tok':>12}" + "".join(f"{title:>9}" for title, _, _ in priced) + f"{'time':>9}  models"
    )
    lines = [header]
    for name, u in rows:
        lines.append(
            f"{name:<{name_width}}{u.calls:>6}{u.input_tokens:>10,}{u.output_tokens:>10,}{u.reasoning_tokens:>11,}"
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
