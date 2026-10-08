import json
from pathlib import Path

from marketplace_evals.reporting.terminal import format_usage
from marketplace_evals.runtimes.claude_code.output import parse_stream_json
from marketplace_evals.sandbox import Sandbox
from marketplace_evals.usage import Usage, model_mismatch, total


def result_event(cost: float, output_tokens: int) -> dict:
    return {
        "type": "result",
        "result": "done",
        "modelUsage": {
            "claude-sonnet-5": {
                "inputTokens": 10,
                "outputTokens": output_tokens,
                "cacheReadInputTokens": 1000,
                "cacheCreationInputTokens": 200,
                "costUSD": cost,
            },
        },
    }


def test_trace_usage_comes_from_the_last_result_which_is_cumulative():
    lines = [json.dumps(result_event(0.10, 50)), json.dumps(result_event(0.19, 90))]
    usage = parse_stream_json(lines, Sandbox(Path("/tmp/sb"), "p"), duration_s=42.0).usage

    assert (usage.input_tokens, usage.output_tokens) == (10, 90)
    assert (usage.cache_read_tokens, usage.cache_write_tokens) == (1000, 200)
    assert usage.cost_usd == 0.19
    assert usage.duration_s == 42.0
    assert usage.calls == 1
    assert usage.models == {"claude-sonnet-5"}


def test_total_adds_everything_up():
    a = Usage(1, 2, 3, 4, 0.5, 10.0, 1, {"sonnet"})
    b = Usage(10, 20, 30, 40, 1.5, 5.0, 2, {"opus"})
    t = total([a, b])
    assert (t.total_tokens, t.cost_usd, t.duration_s, t.calls) == (110, 2.0, 15.0, 3)
    assert t.models == {"sonnet", "opus"}


def test_summary_only_shows_cost_or_credits_when_reported():
    tokens_only = format_usage(Usage(calls=1, reasoning_tokens=5), Usage(), 1)
    assert "reasoning" in tokens_only
    assert "cost $" not in tokens_only and "AI cred" not in tokens_only
    assert "AI cred" in format_usage(Usage(ai_credits=1.0), Usage(), 1)


def test_summary_has_a_row_per_role_and_the_wall_clock():
    summary = format_usage(Usage(calls=6, cost_usd=1.2), Usage(calls=12, cost_usd=0.8), 162)
    assert [line.split()[0] for line in summary.splitlines()[1:4]] == ["agent", "judge", "total"]
    assert "2.00" in summary.splitlines()[3]
    assert "wall clock: 2m42s" in summary


def test_model_mismatch_only_accepts_the_requested_model():
    assert model_mismatch({"claude-sonnet-5-5"}, "claude-sonnet-5-5") is None
    assert model_mismatch(set(), "claude-sonnet-5-5") is None  # nothing reported, nothing to check
    assert "runtime used ['claude-sonnet-5-5']" in model_mismatch({"claude-sonnet-5-5"}, "sonnet")
    assert model_mismatch({"claude-sonnet-5-5", "claude-sonnet-5"}, "claude-sonnet-5-5")
