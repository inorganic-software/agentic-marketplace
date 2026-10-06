import json
import re

from marketplace_evals.reporting.compare import build_matrix, discover, label, load, render_html


def session(name: str, cases: dict, **config) -> dict:
    return {
        "_name": name,
        "started_at": "2026-10-02T10:45:29",
        "config": {
            "provider": "copilot",
            "backend": "vertex",
            "model": "google/gemini-3.8-flash",
            "runs": 3,
            "min_passes": 2,
            **config,
        },
        "repo": {"commit": "dc254cbdeadbeef", "branch": "main", "dirty": False},
        "usage": {"wall_clock_s": 125.4},
        "cases": cases,
    }


def metric(passes: int, runs: int, checks: dict, scores: list, errors: int = 0) -> dict:
    per_run = [{"score": s, "error": None} for s in scores] + [{"score": None, "error": "boom"}] * errors
    return {"passes": passes, "runs": runs, "passed": passes >= 2, "checks": checks, "per_run": per_run}


A = session(
    "20261002-104529",
    {
        "case-a": {
            "plugin": "commons",
            "metrics": {
                "tool_correctness": metric(2, 3, {"calls y": {"passed": 2, "scored": 2}}, [1.0, 0.5], errors=1),
            },
        },
    },
)
B = session(
    "20260930-190120",
    {
        "case-a": {
            "plugin": "commons",
            "metrics": {
                "tool_correctness": metric(1, 1, {"calls y (reworded)": {"passed": 1, "scored": 1}}, [1.0]),
            },
        },
        "case-b": {"plugin": "commons", "metrics": {"rules": metric(3, 3, {}, [1.0, 1.0, 1.0])}},
    },
    runs=1,
    min_passes=1,
)


def test_label_says_session_platform_and_model():
    assert label(A) == "20261002-104529 · copilot/vertex · gemini-3.8-flash"


def test_older_sessions_without_provider_or_backend_still_load(tmp_path):
    folder = tmp_path / "20260930-190120"
    folder.mkdir()
    old = {k: v for k, v in session("ignored", {}).items() if k != "_name"}
    del old["config"]["provider"], old["config"]["backend"]
    (folder / "results.json").write_text(json.dumps(old))

    results = load(folder)  # the folder or the file
    assert label(results) == "20260930-190120 · –/– · gemini-3.8-flash"
    config = {row["label"]: row["values"] for row in build_matrix([results, A])["config"]}
    assert config["provider"] == [None, "copilot"]


def test_the_union_of_goldens_and_checks_with_gaps_where_a_session_lacks_them():
    goldens = build_matrix([A, B])["goldens"]

    assert [(g["id"], g["plugin"]) for g in goldens] == [("case-a", "commons"), ("case-b", "commons")]
    a = goldens[0]["metrics"][0]
    # A reworded check is another check: its text is its key.
    assert [(c["id"], [cell and cell["passed"] for cell in c["cells"]]) for c in a["checks"]] == [
        ("calls y", [2, None]),
        ("calls y (reworded)", [None, 1]),
    ]
    assert goldens[1]["metrics"][0]["cells"][0] is None


def test_rates_compare_sessions_with_a_different_n():
    cells = build_matrix([A, B])["goldens"][0]["metrics"][0]["cells"]
    assert [(c["passed"], c["total"], round(c["rate"], 2)) for c in cells] == [(2, 3, 0.67), (1, 1, 1.0)]


def test_mean_score_leaves_out_the_runs_that_failed():
    cell = build_matrix([A, B])["goldens"][0]["metrics"][0]["cells"][0]
    assert cell["score"] == 0.75 and cell["errors"] == 1 and cell["verdict"] is True


def test_session_cards_and_config_rows():
    matrix = build_matrix([A, B])
    card = matrix["sessions"][1]
    assert (card["metrics_passed"], card["metrics_total"], card["runs"]) == (1, 2, 1)
    config = {row["label"]: row["values"] for row in matrix["config"]}
    assert config["runs / min passes"] == ["3 / 2", "1 / 1"]
    assert config["commit"] == ["dc254cb", "dc254cb"]
    assert config["wall clock"] == ["2m05s", "2m05s"]


def test_the_page_embeds_the_data_even_if_a_check_closes_the_script():
    tricky = session(
        "s",
        {
            "case-a": {
                "plugin": "commons",
                "metrics": {
                    "outcome": metric(
                        3,
                        3,
                        {"no </script><b>x</b> in the code": {"passed": 3, "scored": 3}},
                        [1.0],
                    ),
                },
            }
        },
    )
    page = render_html(build_matrix([tricky, A]))

    embedded = re.search(r'<script id="data" type="application/json">(.*?)</script>', page, re.DOTALL).group(1)
    data = json.loads(embedded)
    assert data["goldens"][0]["metrics"][0]["checks"][0]["id"] == "no </script><b>x</b> in the code"


def test_discovers_every_session_of_a_folder_newest_first(tmp_path):
    for name in ("20260930-190120", "20261002-104529", "20261001-160432"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "results.json").write_text("{}")
    (tmp_path / "20261001-000000").mkdir()  # stopped before writing its results

    # A folder of sessions, a session folder and a file: each session once.
    found = discover([tmp_path, tmp_path / "20260930-190120", tmp_path / "20261001-160432" / "results.json"])
    assert [f.parent.name for f in found] == ["20261002-104529", "20261001-160432", "20260930-190120"]
