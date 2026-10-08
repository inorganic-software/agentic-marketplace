"""Side-by-side comparison of eval sessions, from their `results.json`, as one HTML page.

Laid out like a phone comparison site: the page carries every session of the folder,
and you pick the ones to compare from a dropdown in each empty column. Then a card per
session at the top, and sections (the configuration, then one per golden) with a row per
field, metric or check. Shows what each session got (passed runs out of N, the mean
score), not whether a difference is significant: with N=3 a single run moves the rate a lot.
A session with a baseline also shows, in each cell, the runs that passed without the skill
and the difference, and per golden whether the case passes without the skill. The
metrics of an informative case that are not strict show with ℹ️ and are left out of each
session's count of metrics passed. A case with several prompt variants shows, under each
metric, a row per variant with its runs that passed (and, with a baseline, without the
skill and the difference); checks are not broken down by variant. Each golden also
has a row per efficiency measure: the median per run and, with a baseline, without the
skill and the difference. A golden whose runs made calls the fixture's stubs do not
imitate gets a ⚠ row with how many runs did, per session (from schema 9).

    uv run evals-compare [.runs | .runs/<date> | .runs/<date>/results.json ...] [-o out.html] [--open]

Without arguments it takes every session in `.runs/`. The page is self-contained (data,
CSS and JS inline), so it opens offline and can be attached anywhere. Goldens and checks
missing from a session show as "–"; a check whose text changed in the golden is a
different check, since its text is its key. A required criterion of `outcome` is
marked as such, as the newest session that has it records it.
"""

import argparse
import json
import webbrowser
from pathlib import Path

from marketplace_evals.efficiency import MEASURES, Delta
from marketplace_evals.paths import RUNS_DIR
from marketplace_evals.reporting.terminal import format_duration, format_measure, format_measure_delta

TEMPLATE = Path(__file__).with_name("compare.html")

# (key, label) of the configuration rows, in the order they are shown.
CONFIG_ROWS = [
    ("provider", "provider"),
    ("backend", "backend"),
    ("runtime_version", "runtime"),
    ("model", "agent model"),
    ("judge", "judge"),
    ("judge_model", "judge model"),
    ("runs", "runs / min passes"),
    ("baseline", "baseline (without skill)"),
    ("commit", "commit"),
    ("branch", "branch"),
    ("dirty", "uncommitted changes"),
    ("started_at", "started at"),
    ("wall_clock", "wall clock"),
]

# Label of each efficiency row.
MEASURE_LABELS = {
    "turns": "turns",
    "tool_calls": "tool calls",
    "tokens": "tokens",
    "cost_usd": "cost $",
    "ai_credits": "AI credits",
    "duration_s": "time",
}


def discover(paths: list[Path]) -> list[Path]:
    """The `results.json` of each session, newest first. A path can be a file, a
    session's folder, or a folder of sessions such as `.runs/`."""
    files: set[Path] = set()
    for path in paths:
        if path.is_file():
            files.add(path)
        elif (path / "results.json").is_file():
            files.add(path / "results.json")
        else:
            files.update(path.glob("*/results.json"))
    return sorted(files, key=lambda f: f.parent.name, reverse=True)


def load(path: Path) -> dict:
    """A session's `results.json`, given the file or the session's folder."""
    file = path / "results.json" if path.is_dir() else path
    results = json.loads(file.read_text())
    results["_name"] = file.parent.name
    return results


def label(results: dict) -> str:
    """`<session> · <provider>/<backend> · <model>`. Older sessions have no provider or
    backend: those parts show as "–"."""
    config = results.get("config", {})
    model = (config.get("model") or "–").removeprefix("google/")
    return f"{results['_name']} · {config.get('provider') or '–'}/{config.get('backend') or '–'} · {model}"


def build_matrix(sessions: list[dict]) -> dict:
    """The rows of the comparison: the union of the goldens, metrics and checks of every
    session, in the order they first appear, with one cell per session (None if the
    session does not have it)."""
    goldens: dict[str, dict] = {}
    for i, results in enumerate(sessions):
        for case_id, case in results.get("cases", {}).items():
            golden = goldens.setdefault(
                case_id,
                {
                    "id": case_id,
                    "plugin": case.get("plugin"),
                    "baseline": [None] * len(sessions),
                    "stub_gaps": [None] * len(sessions),
                    "metrics": {},
                    "efficiency": {},
                },
            )
            for measure, cell in _efficiency_cells(case.get("efficiency")).items():
                eff_row = golden["efficiency"].setdefault(
                    measure, {"name": MEASURE_LABELS[measure], "cells": [None] * len(sessions)}
                )
                eff_row["cells"][i] = cell
            golden["stub_gaps"][i] = _stub_gaps_cell(case, results.get("config", {}).get("runs"))
            if "baseline" in case:
                golden["baseline"][i] = {
                    "with": case["baseline"]["with_skill"],
                    "without": case["baseline"]["without_skill"],
                }
            for name, metric in case.get("metrics", {}).items():
                row = golden["metrics"].setdefault(
                    name, {"name": name, "cells": [None] * len(sessions), "variants": {}, "checks": {}}
                )
                row["cells"][i] = _metric_cell(metric, results.get("config", {}).get("min_passes"))
                for variant, cell in _variant_cells(metric).items():
                    variant_row = row["variants"].setdefault(variant, {"id": variant, "cells": [None] * len(sessions)})
                    variant_row["cells"][i] = cell
                without = (metric.get("baseline") or {}).get("without_skill", {}).get("checks", {})
                for check_id, check in metric.get("checks", {}).items():
                    # Sessions come newest first: the row is marked as the newest one records it.
                    check_row = row["checks"].setdefault(
                        check_id,
                        {"id": check_id, "required": bool(check.get("required")), "cells": [None] * len(sessions)},
                    )
                    check_row["cells"][i] = _rate(check["passed"], check["scored"])
                    if check_id in without:
                        w = without[check_id]
                        check_row["cells"][i]["baseline"] = _rate(w["passed"], w["scored"])

    return {
        "sessions": [_session(results) for results in sessions],
        "config": [{"label": text, "values": [_config_value(r, key) for r in sessions]} for key, text in CONFIG_ROWS],
        "goldens": [
            {
                **golden,
                "metrics": [
                    {**m, "variants": list(m["variants"].values()), "checks": list(m["checks"].values())}
                    for m in golden["metrics"].values()
                ],
                "efficiency": [golden["efficiency"][m] for m in MEASURES if m in golden["efficiency"]],
            }
            for golden in goldens.values()
        ],
    }


def _session(results: dict) -> dict:
    config = results.get("config", {})
    # Those of an informative case that are not strict never fail the session: not counted.
    metrics = [
        m
        for case in results.get("cases", {}).values()
        for m in case.get("metrics", {}).values()
        if not m.get("informative")
    ]
    return {
        "label": label(results),
        "name": results["_name"],
        "platform": f"{config.get('provider') or '–'}/{config.get('backend') or '–'}",
        "model": config.get("model"),
        "runs": config.get("runs"),
        "min_passes": config.get("min_passes"),
        "metrics_passed": sum(bool(m.get("passed")) for m in metrics),
        "metrics_total": len(metrics),
    }


def _metric_cell(metric: dict, min_passes: int | None) -> dict:
    """Passed runs out of N, how many were required, and the mean score of the scored
    runs. A run whose agent or judge failed has no score: it is left out of the mean and
    counted in `errors`. Sessions before schema 2 did not record `required` per metric:
    every metric required `min_passes`."""
    per_run = metric.get("per_run", [])
    scores = [r["score"] for r in per_run if r.get("score") is not None]
    cell = {
        **_rate(metric["passes"], metric["runs"]),
        "verdict": bool(metric.get("passed")),
        "informative": bool(metric.get("informative")),
        "required": metric.get("required", min_passes),
        "score": round(sum(scores) / len(scores), 4) if scores else None,
        "errors": sum(1 for r in per_run if r.get("error")),
    }
    if "baseline" in metric:  # a session with a baseline; None if only the skill can pass it
        cell["baseline"] = _baseline_cell(metric["baseline"])
    return cell


def _stub_gaps_cell(case: dict, runs: int | None) -> dict | None:
    """The runs that made a call the fixture's stubs do not imitate, out of all, and with
    a baseline, those without the skill. None without any, or before schema 9."""
    with_skill = case.get("runs_with_stub_gaps") or 0
    without_skill = case.get("runs_with_stub_gaps_without_skill")
    if not with_skill and not without_skill:
        return None
    return {"runs": with_skill, "total": runs, "without": without_skill}


def _variant_cells(metric: dict) -> dict[str, dict | None]:
    """Per prompt variant, its runs that passed, and with a baseline, those without the
    skill and the difference. None for a variant no run got. Empty before schema 7 or
    with a single variant."""
    baseline = metric.get("baseline")
    without = (baseline or {}).get("without_skill", {}).get("variants", {})
    deltas = (baseline or {}).get("variant_deltas", {})
    cells: dict[str, dict | None] = {}
    for variant, tally in metric.get("variants", {}).items():
        if not tally["runs"]:
            cells[variant] = None
            continue
        cell = _rate(tally["passes"], tally["runs"])
        if "baseline" in metric:
            w = without.get(variant)
            cell["baseline"] = (
                None
                if baseline is None or not w or not w["runs"]
                else {
                    **_rate(w["passes"], w["runs"]),
                    "delta": deltas.get(variant),
                }
            )
        cells[variant] = cell
    return cells


def _baseline_cell(baseline: dict | None) -> dict | None:
    """The runs that passed without the skill, and the difference with the skill. Both
    leave out what only the skill can pass, so `with` may differ from the cell's rate."""
    if baseline is None:
        return None
    without = baseline["without_skill"]
    return {
        **_rate(without["passes"], without["runs"]),
        "with_rate": _rate(baseline["with_skill"]["passes"], baseline["with_skill"]["runs"])["rate"],
        "delta": baseline["delta"],
    }


def _efficiency_cells(efficiency: dict | None) -> dict[str, dict]:
    """Per measure a session has a value for, the median with the skill and, with a
    baseline, a note with the median without it and the difference. Empty before schema 8."""
    if not efficiency:
        return {}
    with_skill = efficiency["with_skill"]["median"]
    without_skill = efficiency.get("without_skill", {}).get("median")
    deltas = efficiency.get("delta", {})
    cells = {}
    for measure in MEASURES:
        value = with_skill.get(measure)
        if value is None:
            continue
        cell = {"text": format_measure(measure, value)}
        if without_skill is not None:
            delta = deltas.get(measure)
            cell["note"] = f"w/o {format_measure(measure, without_skill.get(measure))} · Δ " + format_measure_delta(
                measure, None if delta is None else Delta(delta["absolute"], delta["percent"])
            )
        cells[measure] = cell
    return cells


def _rate(passed: int, total: int) -> dict:
    return {"passed": passed, "total": total, "rate": passed / total if total else None}


def _config_value(results: dict, key: str) -> str | None:
    config, repo = results.get("config", {}), results.get("repo", {})
    match key:
        case "runs":
            return f"{config.get('runs')} / {config.get('min_passes')}" if config.get("runs") else None
        case "baseline":
            return "yes" if config.get("baseline") else "no"
        case "commit":
            return (repo.get("commit") or "")[:7] or None
        case "branch":
            return repo.get("branch")
        case "dirty":
            return None if repo.get("dirty") is None else ("yes" if repo["dirty"] else "no")
        case "started_at":
            return results.get("started_at")
        case "wall_clock":
            seconds = results.get("usage", {}).get("wall_clock_s")
            return None if seconds is None else format_duration(seconds)
        case _:
            return config.get(key)


def render_html(matrix: dict) -> str:
    # "</" inside a check's text must not close the <script> holding the data.
    data = json.dumps(matrix, ensure_ascii=False).replace("</", "<\\/")
    return TEMPLATE.read_text().replace("/*__DATA__*/null", data)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "paths",
        type=Path,
        nargs="*",
        default=[RUNS_DIR],
        help="Folders of sessions, session folders or results.json (default: .runs/)",
    )
    parser.add_argument("-o", "--output", type=Path, help="HTML to write (default: .runs/compare.html)")
    parser.add_argument("--open", action="store_true", help="Open the page in the browser")
    args = parser.parse_args()

    # A session that ran no golden (stopped before the first one) has nothing to compare.
    sessions = [s for s in map(load, discover(args.paths)) if s.get("cases")]
    if not sessions:
        parser.error(f"no session with results in {', '.join(map(str, args.paths))}")

    output = args.output or RUNS_DIR / "compare.html"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_html(build_matrix(sessions)))
    print(f"{output} ({len(sessions)} sessions)")
    if args.open:
        webbrowser.open(output.resolve().as_uri())


if __name__ == "__main__":
    main()
