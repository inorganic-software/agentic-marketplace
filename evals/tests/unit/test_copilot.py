import json
from pathlib import Path

import pytest

from marketplace_evals.runtimes.copilot.environment import isolated_env
from marketplace_evals.runtimes.copilot.judge import full_prompt, strip_fences
from marketplace_evals.runtimes.copilot.output import failure, parse_events, usage_from_session
from marketplace_evals.runtimes.copilot.runner import permissions
from marketplace_evals.runtimes.copilot.vertex import Vertex
from marketplace_evals.sandbox import Sandbox

# Copilot reports the resolved path: /tmp is a link to /private/tmp on macOS.
SANDBOX = Sandbox(Path("/tmp/sb"), "commons")
ROOT = "/private/tmp/sb" if SANDBOX.root.resolve() != SANDBOX.root else "/tmp/sb"
ABS = f"{ROOT}/workspace"


def start(id_: str, name: str, arguments: object, model: str = "m1") -> dict:
    return {
        "type": "tool.execution_start",
        "data": {"toolCallId": id_, "toolName": name, "arguments": arguments, "model": model},
    }


def complete(id_: str, success: bool = True) -> dict:
    data = {"toolCallId": id_, "success": success}
    if not success:
        data["error"] = {"message": "Permission denied", "code": "denied"}
    return {"type": "tool.execution_complete", "data": data}


def message(content: str, model: str = "m1") -> dict:
    return {"type": "assistant.message", "data": {"content": content, "model": model}}


RESULT = {"type": "result", "exitCode": 0, "usage": {"premiumRequests": 1}}


def test_maps_tools_to_canonical_actions_and_relative_paths():
    trace = parse_events(
        [
            {"type": "assistant.turn_start", "data": {"turnId": "0"}},
            start("1", "skill", {"skill": "s"}),
            start("2", "view", {"path": f"{ROOT}/plugins/p/skills/s/references/r.md"}),
            start("3", "bash", {"command": f"find {ABS}/src -name '*.java'", "description": "d"}),
            start("4", "rg", {"pattern": "record", "paths": [f"{ABS}/src", "pom.xml"]}),
            start("5", "glob", {"pattern": "**/*.java"}),
            start("6", "sql", {"query": "select 1", "description": "d"}),
            message("", "m1"),
            {"type": "assistant.turn_start", "data": {"turnId": "1"}},
            message("Done", "m1"),
            RESULT,
        ],
        SANDBOX,
    )

    assert [(c.action, c.args) for c in trace.calls] == [
        ("load_skill", {"name": "s"}),
        ("read_file", {"path": "@plugins/p/skills/s/references/r.md"}),
        ("shell", {"command": "find src -name '*.java'"}),
        ("search", {"pattern": "record", "path": "src pom.xml"}),
        ("find_files", {"pattern": "**/*.java"}),
        ("unknown:sql", {"query": "select 1", "description": "d"}),
    ]
    assert trace.final_output == "Done"
    assert trace.turns == {"main": 2}
    assert trace.models == {"main": {"m1"}}


def test_apply_patch_is_one_call_per_file_and_a_new_file_is_a_write():
    patch = (
        f"*** Begin Patch\n*** Update File: {ABS}/src/domain/A.java\n@@\n-a\n+b\n"
        "*** Add File: src/test/ATest.java\n+class ATest {}\n*** End Patch\n"
    )
    trace = parse_events([start("1", "apply_patch", patch), RESULT], SANDBOX)
    assert [(c.action, c.args, c.raw_name) for c in trace.calls] == [
        ("edit_file", {"path": "src/domain/A.java"}, "apply_patch"),
        ("write_file", {"path": "src/test/ATest.java"}, "apply_patch"),
    ]


def test_marks_denied_calls():
    trace = parse_events(
        [
            start("1", "bash", {"command": "touch x"}),
            complete("1", success=False),
            start("2", "view", {"path": f"{ABS}/pom.xml"}),
            complete("2"),
            RESULT,
        ],
        SANDBOX,
    )
    assert [c.denied for c in trace.calls] == [True, False]


def test_reports_every_model_the_session_used():
    trace = parse_events([message("a", "gpt-6-luna"), message("b", "gpt-5.6-luna"), RESULT], SANDBOX)
    assert trace.models == {"main": {"gpt-6-luna", "gpt-5.6-luna"}}


def test_a_session_without_result_failed_even_if_the_cli_exited_0():
    stderr = 'Error: Model "x" from --model flag is not available.\n'
    assert failure([], stderr) == stderr.strip()
    assert failure([{**RESULT, "exitCode": 1}], "boom") == "exit code 1: boom"
    assert failure([RESULT], "") is None


def test_usage_has_tokens_per_model_call_and_the_session_ai_credits():
    otel = [
        {
            "type": "span",
            "name": "chat gpt-6-luna",
            "attributes": {
                "gen_ai.response.model": "gpt-6-luna",
                "gen_ai.usage.input_tokens": 100,
                "gen_ai.usage.output_tokens": 10,
                "gen_ai.usage.cache_read.input_tokens": 60,
            },
        },
        {
            "type": "span",
            "name": "chat gpt-6-luna",
            "attributes": {
                "gen_ai.response.model": "gpt-6-luna",
                "gen_ai.usage.input_tokens": 50,
                "gen_ai.usage.output_tokens": 5,
                "gen_ai.usage.cache_creation.input_tokens": 20,
            },
        },
        {"type": "span", "name": "invoke_agent a", "attributes": {"gen_ai.usage.input_tokens": 150}},
        {"type": "metric", "name": "gen_ai.client.token.usage"},
    ]
    events = [
        {"type": "session.usage_checkpoint", "data": {"totalNanoAiu": 500_000_000}},
        {"type": "session.usage_checkpoint", "data": {"totalNanoAiu": 1_250_000_000}},
        RESULT,
    ]
    usage = usage_from_session(events, otel, duration_s=7.0)

    assert (usage.input_tokens, usage.output_tokens) == (70, 15)
    assert (usage.cache_read_tokens, usage.cache_write_tokens) == (60, 20)
    assert usage.ai_credits == 1.25
    assert usage.cost_usd == 0.0
    assert (usage.duration_s, usage.calls, usage.models) == (7.0, 1, {"gpt-6-luna"})


def test_golden_tools_grant_permission_kinds():
    assert permissions(("read", "edit", "search", "execute")) == ["read", "write", "shell"]
    assert permissions(("search",)) == ["read"]


def test_isolated_env_keeps_the_login_but_no_other_copilot_settings(monkeypatch, tmp_path):
    user_home, home = tmp_path / "user", tmp_path / "run"
    user_home.mkdir()
    home.mkdir()
    (user_home / "config.json").write_text(
        '// managed automatically\n{"lastLoggedInUser": {"host": "h", "login": "me"},'
        ' "loggedInUsers": [{"host": "h", "login": "me"}], "installedPlugins": [{"name": "p"}]}'
    )
    monkeypatch.setenv("COPILOT_HOME", str(user_home))
    monkeypatch.setenv("COPILOT_GITHUB_TOKEN", "t")
    monkeypatch.setenv("COPILOT_MODEL", "other")
    monkeypatch.setenv("COPILOT_ALLOW_ALL", "true")
    monkeypatch.setenv("GITHUB_COPILOT_PROMPT_MODE_REPO_HOOKS", "true")
    env = isolated_env(home, home / "otel.jsonl", "m")
    assert env["COPILOT_GITHUB_TOKEN"] == "t"
    assert env["COPILOT_HOME"] == str(home)
    assert env["COPILOT_OTEL_FILE_EXPORTER_PATH"] == str(home / "otel.jsonl")
    assert not {"COPILOT_MODEL", "COPILOT_ALLOW_ALL", "GITHUB_COPILOT_PROMPT_MODE_REPO_HOOKS"} & set(env)
    assert json.loads((home / "config.json").read_text()) == {
        "lastLoggedInUser": {"host": "h", "login": "me"},
        "loggedInUsers": [{"host": "h", "login": "me"}],
    }


def test_judge_prompt_carries_the_schema_and_fences_are_stripped():
    assert '{"type": "object"}' in full_prompt("Judge this", {"type": "object"})
    assert "Schema" not in full_prompt("Judge this", None)
    assert strip_fences('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert strip_fences('{"a": 1}') == '{"a": 1}'


def test_a_skill_reads_and_loads_the_same_as_on_claude_code():
    """The goldens expect the same calls on both runtimes."""
    from marketplace_evals.runtimes.claude_code.output import parse_stream_json

    skill_md = "plugins/commons/skills/git-workflow/SKILL.md"
    copilot = parse_events(
        [
            start("1", "view", {"path": f"{ROOT}/{skill_md}"}),
            start("2", "skill", {"skill": "git-workflow"}),
            RESULT,
        ],
        SANDBOX,
    )
    claude = parse_stream_json(
        [
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "1",
                                "name": "Read",
                                "input": {"file_path": f"/tmp/sb/{skill_md}"},
                            },
                            {
                                "type": "tool_use",
                                "id": "2",
                                "name": "Skill",
                                "input": {"skill": "commons:git-workflow"},
                            },
                        ]
                    },
                }
            )
        ],
        SANDBOX,
    )
    assert (
        [(c.action, c.args) for c in copilot.calls]
        == [(c.action, c.args) for c in claude.calls]
        == [
            ("read_file", {"path": "@plugins/commons/skills/git-workflow/SKILL.md"}),
            ("load_skill", {"name": "git-workflow"}),
        ]
    )


VERTEX = Vertex(project="p", location="global")


def test_isolated_env_on_vertex_uses_byok_and_no_github_login(monkeypatch, tmp_path):
    user_home, home = tmp_path / "user", tmp_path / "run"
    user_home.mkdir()
    home.mkdir()
    (user_home / "config.json").write_text('{"lastLoggedInUser": {"host": "h", "login": "me"}}')
    monkeypatch.setenv("COPILOT_HOME", str(user_home))
    for name in ("COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"):
        monkeypatch.setenv(name, "t")
    monkeypatch.setenv("COPILOT_PROVIDER_API_KEY", "stale")
    env = isolated_env(home, home / "otel.jsonl", "google/gemini-x", VERTEX)

    assert not {"COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN", "COPILOT_PROVIDER_API_KEY"} & set(env)
    assert not (home / "config.json").exists()
    assert env["COPILOT_PROVIDER_TYPE"] == "openai"
    assert env["COPILOT_PROVIDER_BASE_URL"] == VERTEX.base_url
    assert env["COPILOT_PROVIDER_WIRE_MODEL"] == "google/gemini-x"
    assert "print-access-token" in env["COPILOT_PROVIDER_API_KEY_COMMAND"]


def test_vertex_endpoint_is_global_or_regional():
    assert VERTEX.base_url == "https://aiplatform.googleapis.com/v1/projects/p/locations/global/endpoints/openapi"
    assert Vertex("p", "europe-west4").base_url.startswith("https://europe-west4-aiplatform.googleapis.com/")


def test_vertex_needs_the_project_and_the_location(monkeypatch):
    monkeypatch.setenv("EVALS_VERTEX_PROJECT", "p")
    monkeypatch.delenv("EVALS_VERTEX_LOCATION", raising=False)
    with pytest.raises(ValueError, match="EVALS_VERTEX_LOCATION"):
        Vertex.from_env()
    monkeypatch.setenv("EVALS_VERTEX_LOCATION", "global")
    assert Vertex.from_env() == VERTEX


def test_reasoning_tokens_are_counted_apart_from_output():
    otel = [
        {
            "type": "span",
            "name": "chat google/gemini-x",
            "attributes": {
                "gen_ai.response.model": "google/gemini-x",
                "gen_ai.usage.input_tokens": 100,
                "gen_ai.usage.output_tokens": 10,
                "gen_ai.usage.reasoning.output_tokens": 30,
            },
        }
    ]
    usage = usage_from_session([RESULT], otel, duration_s=1.0)
    assert (usage.output_tokens, usage.reasoning_tokens, usage.total_tokens) == (10, 30, 140)
    assert usage.ai_credits == 0.0
