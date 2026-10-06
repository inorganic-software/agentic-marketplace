"""Side-by-side comparison of eval sessions, from their `results.json`, as one HTML page.

Laid out like a phone comparison site: the page carries every session of the folder,
and you pick the ones to compare from a dropdown in each empty column. Then a card per
session at the top, and sections (the configuration, then one per golden) with a row per
field, metric or check. Shows what each session got (passed runs out of N, the mean
score), not whether a difference is significant: with N=3 a single run moves the rate a lot.

    uv run evals-compare [.runs | .runs/<date> | .runs/<date>/results.json ...] [-o out.html] [--open]

Without arguments it takes every session in `.runs/`. The page is self-contained (data,
CSS and JS inline), so it opens offline and can be attached anywhere. Goldens and checks
missing from a session show as "–"; a check whose text changed in the golden is a
different check, since its text is its key.
"""

import argparse
import json
import webbrowser
from pathlib import Path

from marketplace_evals.paths import RUNS_DIR
from marketplace_evals.reporting.terminal import format_duration

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
    ("commit", "commit"),
    ("branch", "branch"),
    ("dirty", "uncommitted changes"),
    ("started_at", "started at"),
    ("wall_clock", "wall clock"),
]


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
            golden = goldens.setdefault(case_id, {"id": case_id, "plugin": case.get("plugin"), "metrics": {}})
            for name, metric in case.get("metrics", {}).items():
                row = golden["metrics"].setdefault(name, {"name": name, "cells": [None] * len(sessions), "checks": {}})
                row["cells"][i] = _metric_cell(metric)
                for check_id, check in metric.get("checks", {}).items():
                    check_row = row["checks"].setdefault(check_id, {"id": check_id, "cells": [None] * len(sessions)})
                    check_row["cells"][i] = _rate(check["passed"], check["scored"])

    return {
        "sessions": [_session(results) for results in sessions],
        "config": [{"label": text, "values": [_config_value(r, key) for r in sessions]} for key, text in CONFIG_ROWS],
        "goldens": [
            {
                **golden,
                "metrics": [{**m, "checks": list(m["checks"].values())} for m in golden["metrics"].values()],
            }
            for golden in goldens.values()
        ],
    }


def _session(results: dict) -> dict:
    config = results.get("config", {})
    metrics = [m for case in results.get("cases", {}).values() for m in case.get("metrics", {}).values()]
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


def _metric_cell(metric: dict) -> dict:
    """Passed runs out of N and the mean score of the scored runs. A run whose agent or
    judge failed has no score: it is left out of the mean and counted in `errors`."""
    per_run = metric.get("per_run", [])
    scores = [r["score"] for r in per_run if r.get("score") is not None]
    return {
        **_rate(metric["passes"], metric["runs"]),
        "verdict": bool(metric.get("passed")),
        "score": round(sum(scores) / len(scores), 4) if scores else None,
        "errors": sum(1 for r in per_run if r.get("error")),
    }


def _rate(passed: int, total: int) -> dict:
    return {"passed": passed, "total": total, "rate": passed / total if total else None}


def _config_value(results: dict, key: str) -> str | None:
    config, repo = results.get("config", {}), results.get("repo", {})
    match key:
        case "runs":
            return f"{config.get('runs')} / {config.get('min_passes')}" if config.get("runs") else None
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
