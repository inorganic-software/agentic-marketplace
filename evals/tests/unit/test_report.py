import copy

from marketplace_evals.reporting.markdown import MARKER, render

RESULTS = {
    "config": {
        "runtime": "copilot",
        "runtime_version": "GitHub Copilot CLI 1.0.91.",
        "backend": "vertex",
        "model": "google/gemini-x",
        "judge_model": "google/gemini-x",
        "runs": 3,
        "min_passes": 2,
    },
    "repo": {"commit": "abcdef1234", "branch": "b", "dirty": False},
    "usage": {
        "agent": {
            "input_tokens": 100,
            "output_tokens": 10,
            "reasoning_tokens": 5,
            "cache_read_tokens": 1000,
            "cache_write_tokens": 0,
            "calls": 3,
        },
        "judge": {
            "input_tokens": 50,
            "output_tokens": 5,
            "reasoning_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "calls": 6,
        },
        "wall_clock_s": 125.4,
    },
    "cases": {
        "commons/git-workflow/case-a": {
            "plugin": "commons",
            "skill": "git-workflow",
            "metrics": {
                "forbidden_calls": {"passed": True, "passes": 3, "runs": 3, "required": 3, "strict": True},
                "outcome": {"passed": False, "passes": 1, "runs": 3, "required": 2, "strict": False},
            },
        },
        "commons/git-workflow/case-b": {
            "plugin": "commons",
            "skill": "git-workflow",
            "metrics": {
                # Before schema 2 a metric did not record `required`: it was `min_passes`.
                "forbidden_calls": {"passed": True, "passes": 2, "runs": 3},
            },
        },
    },
}


def test_a_row_per_golden_and_a_column_per_metric():
    report = render(RESULTS)
    assert report.startswith(MARKER)
    assert "❌ 2/3 metrics passed" in report
    assert "2 of 3 runs must pass, all 3 for prohibitions (strict)" in report
    assert "| Golden | forbidden_calls | outcome |" in report
    assert "| `commons/git-workflow/case-a` | ✅ 3/3 (3 req.) | ❌ 1/3 (2 req.) |" in report
    assert "| `commons/git-workflow/case-b` | ✅ 2/3 (2 req.) | – |" in report


def test_says_what_ran_and_what_it_used():
    report = render(RESULTS, run_url="https://ci/run/1")
    assert "backend `vertex`" in report and "commit `abcdef1`" in report
    assert "1,170 tokens" in report and "wall clock 2m05s" in report
    assert "[workflow run](https://ci/run/1)" in report


def test_with_a_baseline_cells_and_cases_say_how_they_did_without_the_skill():
    results = copy.deepcopy(RESULTS)
    results["config"]["baseline"] = True
    case_a = results["cases"]["commons/git-workflow/case-a"]
    case_a["baseline"] = {"with_skill": False, "without_skill": False}
    case_a["metrics"]["forbidden_calls"]["baseline"] = None
    case_a["metrics"]["outcome"]["baseline"] = {
        "with_skill": {"passes": 1, "runs": 3},
        "without_skill": {"passes": 0, "runs": 3},
        "delta": 0.3333,
    }
    results["cases"]["commons/git-workflow/case-b"]["baseline"] = {"with_skill": True, "without_skill": True}
    report = render(results)

    assert "| Golden | Without skill | forbidden_calls | outcome |" in report
    assert (
        "| `commons/git-workflow/case-a` | ❌ | ✅ 3/3 (3 req.) · w/o – | ❌ 1/3 (2 req.) · w/o 0/3 · Δ +33 pp |"
        in report
    )
    assert "| `commons/git-workflow/case-b` | ✅ ⚠ does not measure the skill | ✅ 2/3 (2 req.) · w/o – | – |" in report


def test_skill_loading_has_a_table_per_skill_and_kind():
    results = copy.deepcopy(RESULTS)
    results["skill_loading"] = {
        "commons/git-workflow": {
            "should_load": {"prompts": 5, "prompts_passed": 4, "runs": 15, "runs_passed": 13},
            "should_not_load": {"prompts": 0, "prompts_passed": 0, "runs": 0, "runs_passed": 0},
        }
    }
    report = render(results)
    assert "| Skill | Should load | Should not load |" in report
    assert "| `commons/git-workflow` | ❌ 4/5 (13/15 runs) | – |" in report
    assert "Should load" not in render(RESULTS)  # sessions before schema 4 have none


def test_informative_metrics_show_apart_and_do_not_count_in_the_title():
    results = copy.deepcopy(RESULTS)
    results["cases"]["commons/git-workflow/case-c"] = {
        "plugin": "commons",
        "skill": "git-workflow",
        "informative": True,
        "metrics": {
            "outcome_checks": {"passed": False, "passes": 1, "runs": 3, "required": 2, "informative": True},
            "rules_always_met": {"passed": True, "passes": 3, "runs": 3, "required": 3, "strict": True},
        },
    }
    report = render(results)
    assert "❌ 3/4 metrics passed" in report  # case-c's prohibition counts, its outcome_checks does not
    assert "Informative: 0/1 metrics of informative cases reached the minimum" in report
    assert "ℹ️ 1/3 (2 req.)" in report
    assert "Informative:" not in render(RESULTS)
