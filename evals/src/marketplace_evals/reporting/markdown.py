"""Markdown report of an eval session, from its `results.json`.

CI posts it as a PR comment and as the job summary. It only has what decides the
result (each golden's metrics, passed runs out of N) and the usage; the judge's
reasons and the offending calls are in `summary.txt`, uploaded with the logs. A session
with a baseline also shows how each case and metric did without the skill, and one with
`skill_loading` prompts, how many of each kind passed per skill. The metrics of an
informative case that are not strict show with ℹ️ and are left out of the count in the
title: they never fail the session.

    uv run evals-report .runs/<date>/results.json [--run-url URL]
"""

import argparse
import json
from pathlib import Path

from marketplace_evals.reporting.terminal import format_delta, format_duration

# Marks the PR comment, so CI updates it instead of adding a new one per push.
MARKER = "<!-- marketplace-evals -->"


def render(results: dict, run_url: str | None = None) -> str:
    config, cases = results["config"], results["cases"]
    metrics = sorted({m for case in cases.values() for m in case["metrics"]})
    all_metrics = [m for case in cases.values() for m in case["metrics"].values()]
    required = [m for m in all_metrics if not m.get("informative")]
    informative = [m for m in all_metrics if m.get("informative")]
    passed, total = sum(m["passed"] for m in required), len(required)
    repo = results["repo"]
    baseline = bool(config.get("baseline"))
    columns = ["Golden", *(["Without skill"] if baseline else []), *metrics]

    lines = [
        MARKER,
        f"## Evals: {'✅' if passed == total else '❌'} {passed}/{total} metrics passed",
        "",
        f"{config['runtime_version'] or config['runtime']} · backend `{config['backend']}` · "
        f"agent `{config['model']}` · judge `{config['judge_model']}` · "
        f"{config['min_passes']} of {config['runs']} runs must pass, all {config['runs']} for prohibitions (strict) · "
        f"commit `{(repo['commit'] or '?')[:7]}`{' (dirty)' if repo['dirty'] else ''}",
        "",
        *(
            [
                "Baseline: each case also ran without its skill. Each cell adds the runs that passed "
                "without it and the difference (Δ), both leaving out what only the skill can pass; "
                "it does not change whether the case passes.",
                "",
            ]
            if baseline
            else []
        ),
        *(
            [
                f"Informative: {sum(m['passed'] for m in informative)}/{len(informative)} metrics of "
                "informative cases reached the minimum (ℹ️). They measure what the agent does not do well "
                "yet and never fail the session; their prohibitions are counted above.",
                "",
            ]
            if informative
            else []
        ),
        "| " + " | ".join(columns) + " |",
        "|---|" + "---|" * (len(columns) - 1),
    ]
    for case_id, case in cases.items():
        cells = [_cell(case["metrics"].get(m), config["min_passes"], baseline) for m in metrics]
        if baseline:
            cells.insert(0, _case_baseline(case.get("baseline")))
        lines.append(f"| `{case_id}` | " + " | ".join(cells) + " |")
    if loading := results.get("skill_loading"):
        lines += [
            "",
            "Skill loading: the golden's prompts that should load the skill and that should not, "
            "how many passed, and how many of their runs.",
            "",
            "| Skill | Should load | Should not load |",
            "|---|---|---|",
            *(
                f"| `{skill}` | {_tally(t['should_load'])} | {_tally(t['should_not_load'])} |"
                for skill, t in loading.items()
            ),
        ]
    lines += ["", _usage(results["usage"])]
    if run_url:
        lines += [
            "",
            f"Judge reasons, failed checks and logs: [workflow run]({run_url}) (`summary.txt` in the artifact).",
        ]
    return "\n".join(lines) + "\n"


def _cell(metric: dict | None, min_passes: int, baseline: bool = False) -> str:
    """Passed runs out of N, and how many were required. Sessions before schema 2 did
    not record it per metric: every metric required `min_passes`. With a baseline, also
    the runs that passed without the skill and the difference."""
    if metric is None:
        return "–"
    required = metric.get("required", min_passes)
    mark = "ℹ️" if metric.get("informative") else "✅" if metric["passed"] else "❌"
    cell = f"{mark} {metric['passes']}/{metric['runs']} ({required} req.)"
    if not baseline:
        return cell
    if (b := metric.get("baseline")) is None:
        return f"{cell} · w/o –"
    without = b["without_skill"]
    return f"{cell} · w/o {without['passes']}/{without['runs']} · Δ {format_delta(b['delta'])}"


def _tally(tally: dict) -> str:
    """`✅ 5/5 (15/15 runs)`, or `–` without prompts of that kind."""
    if not tally["prompts"]:
        return "–"
    mark = "✅" if tally["prompts_passed"] == tally["prompts"] else "❌"
    return f"{mark} {tally['prompts_passed']}/{tally['prompts']} ({tally['runs_passed']}/{tally['runs']} runs)"


def _case_baseline(baseline: dict | None) -> str:
    """Whether the case passes without its skill, warning when it does."""
    if baseline is None:
        return "–"
    return "✅ ⚠ does not measure the skill" if baseline["without_skill"] else "❌"


def _usage(usage: dict) -> str:
    def tokens(u: dict) -> int:
        return sum(
            u[k]
            for k in (
                "input_tokens",
                "output_tokens",
                "reasoning_tokens",
                "cache_read_tokens",
                "cache_write_tokens",
            )
        )

    # From schema 8 the runs without the skill are apart from the agent's; before, inside.
    agents = [usage["agent"], *([usage["agent_without_skill"]] if "agent_without_skill" in usage else [])]
    agent_tokens, agent_runs = sum(tokens(a) for a in agents), sum(a["calls"] for a in agents)
    judge = usage["judge"]
    return (
        f"Usage: {agent_tokens + tokens(judge):,} tokens "
        f"(agent {agent_tokens:,} in {agent_runs} runs, judge {tokens(judge):,} "
        f"in {judge['calls']} calls) · wall clock {format_duration(usage['wall_clock_s'])}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("results", type=Path, help="results.json of an eval session")
    parser.add_argument("--run-url", help="Link to the CI run with the full report")
    args = parser.parse_args()
    print(render(json.loads(args.results.read_text()), args.run_url), end="")


if __name__ == "__main__":
    main()
