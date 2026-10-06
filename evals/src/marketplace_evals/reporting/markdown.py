"""Markdown report of an eval session, from its `results.json`.

CI posts it as a PR comment and as the job summary. It only has what decides the
result (each golden's metrics, passed runs out of N) and the usage; the judge's
reasons and the offending calls are in `summary.txt`, uploaded with the logs.

    uv run evals-report .runs/<date>/results.json [--run-url URL]
"""

import argparse
import json
from pathlib import Path

from marketplace_evals.reporting.terminal import format_duration

# Marks the PR comment, so CI updates it instead of adding a new one per push.
MARKER = "<!-- marketplace-evals -->"


def render(results: dict, run_url: str | None = None) -> str:
    config, cases = results["config"], results["cases"]
    metrics = sorted({m for case in cases.values() for m in case["metrics"]})
    passed = sum(m["passed"] for case in cases.values() for m in case["metrics"].values())
    total = sum(len(case["metrics"]) for case in cases.values())
    repo = results["repo"]

    lines = [
        MARKER,
        f"## Evals: {'✅' if passed == total else '❌'} {passed}/{total} metrics passed",
        "",
        f"{config['runtime_version'] or config['runtime']} · backend `{config['backend']}` · "
        f"agent `{config['model']}` · judge `{config['judge_model']}` · "
        f"{config['min_passes']} of {config['runs']} runs must pass · "
        f"commit `{(repo['commit'] or '?')[:7]}`{' (dirty)' if repo['dirty'] else ''}",
        "",
        "| Golden | " + " | ".join(metrics) + " |",
        "|---|" + "---|" * len(metrics),
    ]
    for case_id, case in cases.items():
        cells = [_cell(case["metrics"].get(m)) for m in metrics]
        lines.append(f"| `{case_id}` | " + " | ".join(cells) + " |")
    lines += ["", _usage(results["usage"])]
    if run_url:
        lines += [
            "",
            f"Judge reasons, failed checks and logs: [workflow run]({run_url}) (`summary.txt` in the artifact).",
        ]
    return "\n".join(lines) + "\n"


def _cell(metric: dict | None) -> str:
    if metric is None:
        return "–"
    return f"{'✅' if metric['passed'] else '❌'} {metric['passes']}/{metric['runs']}"


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

    agent, judge = usage["agent"], usage["judge"]
    return (
        f"Usage: {tokens(agent) + tokens(judge):,} tokens "
        f"(agent {tokens(agent):,} in {agent['calls']} runs, judge {tokens(judge):,} "
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
