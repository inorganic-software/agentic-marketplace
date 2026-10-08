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
                "expected_calls": metric(2, 3, {"calls y": {"passed": 2, "scored": 2}}, [1.0, 0.5], errors=1),
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
                "expected_calls": metric(1, 1, {"calls y (reworded)": {"passed": 1, "scored": 1}}, [1.0]),
            },
        },
        "case-b": {"plugin": "commons", "metrics": {"rules_always_met_judged": metric(3, 3, {}, [1.0, 1.0, 1.0])}},
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


def test_a_required_criterion_is_marked_as_the_newest_session_records_it():
    def outcome(required: bool | None) -> dict:
        check = {"passed": 1, "scored": 1} | ({} if required is None else {"required": required})
        return {"case-a": {"plugin": "commons", "metrics": {"outcome": metric(1, 1, {"is imperative": check}, [1.0])}}}

    newest, older = session("2", outcome(True)), session("1", outcome(False))
    [check] = build_matrix([newest, older])["goldens"][0]["metrics"][0]["checks"]
    assert check["required"] is True
    # Marking it required keeps its row: its key is its text.
    assert [cell["passed"] for cell in check["cells"]] == [1, 1]
    # Before schema 6 nothing was recorded: not marked.
    [check] = build_matrix([session("0", outcome(None))])["goldens"][0]["metrics"][0]["checks"]
    assert check["required"] is False


def test_rates_compare_sessions_with_a_different_n():
    cells = build_matrix([A, B])["goldens"][0]["metrics"][0]["cells"]
    assert [(c["passed"], c["total"], round(c["rate"], 2)) for c in cells] == [(2, 3, 0.67), (1, 1, 1.0)]


def test_cells_say_how_many_runs_were_required():
    strict = session(
        "s",
        {"case-a": {"plugin": "commons", "metrics": {"forbidden_calls": {**metric(3, 3, {}, [1.0]), "required": 3}}}},
    )
    assert build_matrix([strict])["goldens"][0]["metrics"][0]["cells"][0]["required"] == 3
    # Before schema 2 a metric did not record it: it was the session's min_passes.
    assert [c["required"] for c in build_matrix([A, B])["goldens"][0]["metrics"][0]["cells"]] == [2, 1]


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


def test_with_a_baseline_cells_show_the_runs_without_the_skill_and_the_difference():
    outcome = metric(3, 3, {"x": {"passed": 3, "scored": 3}, "only with": {"passed": 3, "scored": 3}}, [1.0])
    outcome["baseline"] = {
        "with_skill": {"passes": 3, "runs": 3},
        "without_skill": {"passes": 1, "runs": 3, "checks": {"x": {"passed": 1, "scored": 3}}},
        "delta": 0.6667,
    }
    calls = {**metric(3, 3, {}, [1.0]), "baseline": None}
    with_baseline = session(
        "s",
        {
            "case-a": {
                "plugin": "commons",
                "baseline": {"with_skill": True, "without_skill": False},
                "metrics": {"outcome": outcome, "expected_calls": calls},
            }
        },
        baseline=True,
    )
    matrix = build_matrix([with_baseline, A])
    golden = matrix["goldens"][0]

    assert golden["baseline"] == [{"with": True, "without": False}, None]
    rows = {m["name"]: m for m in golden["metrics"]}
    cell = rows["outcome"]["cells"][0]["baseline"]
    assert (cell["passed"], cell["total"], cell["with_rate"], cell["delta"]) == (1, 3, 1.0, 0.6667)
    checks = {c["id"]: c["cells"][0] for c in rows["outcome"]["checks"]}
    assert checks["x"]["baseline"]["passed"] == 1 and "baseline" not in checks["only with"]
    assert rows["expected_calls"]["cells"][0]["baseline"] is None  # only the skill can pass it
    assert "baseline" not in rows["expected_calls"]["cells"][1]  # a session without baseline
    config = {row["label"]: row["values"] for row in matrix["config"]}
    assert config["baseline (without skill)"] == ["yes", "no"]


def test_informative_metrics_are_marked_and_left_out_of_the_session_count():
    informative = {**metric(0, 3, {}, [0.0, 0.0, 0.0]), "informative": True}
    s = session("20261008-100000", {"case-c": {"plugin": "commons", "metrics": {"outcome_checks": informative}}})
    matrix = build_matrix([s])
    assert matrix["goldens"][0]["metrics"][0]["cells"][0]["informative"] is True
    assert (matrix["sessions"][0]["metrics_passed"], matrix["sessions"][0]["metrics_total"]) == (0, 0)


def test_a_metric_with_variants_has_a_row_per_variant_with_and_without_the_skill():
    outcome = metric(2, 3, {}, [1.0])
    outcome["variants"] = {"a": {"passes": 2, "runs": 2}, "b": {"passes": 0, "runs": 1}, "c": {"passes": 0, "runs": 0}}
    outcome["baseline"] = {
        "with_skill": {"passes": 2, "runs": 3},
        "without_skill": {
            "passes": 1,
            "runs": 3,
            "variants": {"a": {"passes": 1, "runs": 2}, "b": {"passes": 0, "runs": 1}, "c": {"passes": 0, "runs": 0}},
        },
        "delta": 0.3333,
        "variant_deltas": {"a": 0.5, "b": 0, "c": None},
    }
    with_variants = session("s", {"case-a": {"plugin": "commons", "metrics": {"outcome": outcome}}}, baseline=True)
    matrix = build_matrix([with_variants, A])
    [row] = [m for m in matrix["goldens"][0]["metrics"] if m["name"] == "outcome"]
    variants = {v["id"]: v["cells"] for v in row["variants"]}
    assert list(variants) == ["a", "b", "c"]
    a = variants["a"][0]
    assert (a["passed"], a["total"], a["baseline"]["passed"], a["baseline"]["delta"]) == (2, 2, 1, 0.5)
    assert variants["c"] == [None, None]  # no run got it; A has no such metric
    calls = next(m for m in matrix["goldens"][0]["metrics"] if m["name"] == "expected_calls")
    assert calls["variants"] == []  # a session before schema 7
