import json
from pathlib import Path

from marketplace_evals.runtimes.claude_code.output import parse_stream_json
from marketplace_evals.runtimes.claude_code.runner import allowed_tools
from marketplace_evals.sandbox import Sandbox

SANDBOX = Sandbox(Path("/tmp/sb"), "commons")
# The runtime may report the resolved path: /tmp is a link to /private/tmp on macOS.
ROOT = "/private/tmp/sb" if SANDBOX.root.resolve() != SANDBOX.root else "/tmp/sb"


def events(*evs: dict) -> list[str]:
    return [json.dumps(e) for e in evs]


def tool_use(id_: str, name: str, **input_) -> dict:
    return {
        "type": "assistant",
        "message": {"content": [{"type": "tool_use", "id": id_, "name": name, "input": input_}]},
    }


def test_maps_tools_to_canonical_actions_and_relative_paths():
    trace = parse_stream_json(
        events(
            {"type": "system", "subtype": "init"},
            tool_use("1", "Read", file_path="/tmp/sb/workspace/front/a.ts"),
            tool_use("2", "Bash", command="git status"),
            tool_use("3", "Read", file_path=f"{ROOT}/plugins/commons/skills/s/SKILL.md"),
            tool_use("4", "WebFetch", url="https://x"),
            {"type": "result", "result": "report"},
        ),
        SANDBOX,
    )

    assert [(c.action, c.args) for c in trace.calls] == [
        ("read_file", {"path": "front/a.ts"}),
        ("shell", {"command": "git status"}),
        ("read_file", {"path": "@plugins/commons/skills/s/SKILL.md"}),
        ("unknown:WebFetch", {"url": "https://x"}),
    ]
    assert trace.final_output == "report"


def test_a_plugin_skill_is_loaded_by_its_name_without_the_plugin():
    trace = parse_stream_json(events(tool_use("1", "Skill", skill="commons:git-workflow")), SANDBOX)
    assert [(c.action, c.args) for c in trace.calls] == [("load_skill", {"name": "git-workflow"})]


def test_paths_in_the_sandbox_outside_the_workspace_get_a_neutral_prefix():
    trace = parse_stream_json(
        events(
            tool_use("1", "Bash", command=f"git clone {ROOT}/origin.git x && cd {ROOT}/workspace"),
            tool_use("2", "Grep", pattern="x", path="./src"),
        ),
        SANDBOX,
    )
    assert [c.args for c in trace.calls] == [
        {"command": "git clone @sandbox/origin.git x && cd ."},
        {"pattern": "x", "path": "src"},
    ]


def test_marks_denied_calls():
    trace = parse_stream_json(
        events(
            tool_use("1", "Bash", command="gh api x"),
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "1",
                            "is_error": True,
                            "content": "denied",
                        }
                    ]
                },
            },
        ),
        SANDBOX,
    )
    assert trace.calls[0].denied


def test_attributes_subagent_calls_to_the_delegated_agent():
    sub = tool_use("s1", "Read", file_path="/tmp/sb/workspace/front/a.ts")
    sub["parent_tool_use_id"] = "d1"
    sub["message"]["model"] = "claude-sonnet-5"
    main = tool_use("d1", "Agent", subagent_type="reviewer", prompt="review")
    main["message"]["model"] = "claude-haiku-4-5"

    trace = parse_stream_json(events(main, sub), SANDBOX)

    assert [(c.by, c.action, c.args) for c in trace.calls] == [
        ("main", "delegate", {"agent": "reviewer"}),
        ("reviewer", "read_file", {"path": "front/a.ts"}),
    ]
    assert trace.models == {"main": {"claude-haiku-4-5"}, "reviewer": {"claude-sonnet-5"}}


def test_golden_tools_become_claude_code_tools_plus_skill():
    assert allowed_tools(("read", "edit", "search", "execute")) == [
        "Read",
        "Edit",
        "Write",
        "Grep",
        "Glob",
        "Bash",
        "Skill",
    ]
    assert allowed_tools(("read",)) == ["Read", "Skill"]


def test_captures_subagent_report_from_sync_result_and_background_notification():
    sync = parse_stream_json(
        events(
            tool_use("d1", "Agent", subagent_type="rev"),
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "d1",
                            "content": [
                                {
                                    "type": "text",
                                    "text": "Report on /tmp/sb/workspace/front/a.ts",
                                }
                            ],
                        }
                    ]
                },
            },
        ),
        SANDBOX,
    )
    assert sync.reports == {"rev": "Report on front/a.ts"}

    background = parse_stream_json(
        events(
            tool_use("d1", "Agent", subagent_type="rev"),
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "d1",
                            "content": [
                                {
                                    "type": "text",
                                    "text": "Async agent launched successfully.",
                                }
                            ],
                        }
                    ]
                },
            },
            {
                "type": "system",
                "subtype": "task_notification",
                "tool_use_id": "d1",
                "status": "completed",
                "summary": "Final report",
            },
        ),
        SANDBOX,
    )
    assert background.reports == {"rev": "Final report"}


def test_counts_turns_per_agent_as_distinct_replies():
    def reply(msg_id: str, tool_id: str, name: str, parent: str | None = None, **input_) -> dict:
        ev = tool_use(tool_id, name, **input_)
        ev["message"]["id"] = msg_id
        if parent:
            ev["parent_tool_use_id"] = parent
        return ev

    trace = parse_stream_json(
        events(
            reply("m1", "d1", "Agent", subagent_type="rev"),
            reply("s1", "t1", "Read", parent="d1", file_path="/tmp/sb/workspace/a.ts"),
            reply("s1", "t2", "Read", parent="d1", file_path="/tmp/sb/workspace/b.ts"),  # same reply, 2 tools
            reply("s2", "t3", "Grep", parent="d1", pattern="x"),
        ),
        SANDBOX,
    )

    assert trace.turns == {"main": 1, "rev": 2}
    assert trace.tool_calls() == {"main": 1, "rev": 3}
