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
                "tool_correctness": {"passed": True, "passes": 3, "runs": 3},
                "outcome": {"passed": False, "passes": 1, "runs": 3},
            },
        },
        "commons/git-workflow/case-b": {
            "plugin": "commons",
            "skill": "git-workflow",
            "metrics": {
                "tool_correctness": {"passed": True, "passes": 2, "runs": 3},
            },
        },
    },
}


def test_a_row_per_golden_and_a_column_per_metric():
    report = render(RESULTS)
    assert report.startswith(MARKER)
    assert "❌ 2/3 metrics passed" in report
    assert "| Golden | outcome | tool_correctness |" in report
    assert "| `commons/git-workflow/case-a` | ❌ 1/3 | ✅ 3/3 |" in report
    assert "| `commons/git-workflow/case-b` | – | ✅ 2/3 |" in report


def test_says_what_ran_and_what_it_used():
    report = render(RESULTS, run_url="https://ci/run/1")
    assert "backend `vertex`" in report and "commit `abcdef1`" in report
    assert "1,170 tokens" in report and "wall clock 2m05s" in report
    assert "[workflow run](https://ci/run/1)" in report
