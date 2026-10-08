"""Reads Claude Code's output: the agent's `stream-json` and the judge's `json`."""

import json

from marketplace_evals.runtimes.canonical import ToolMap, to_canonical
from marketplace_evals.sandbox import Sandbox
from marketplace_evals.trace import (
    DELEGATE,
    EDIT_FILE,
    FIND_FILES,
    LOAD_SKILL,
    MAIN_AGENT,
    READ_FILE,
    SEARCH,
    SHELL,
    WRITE_FILE,
    Trace,
)
from marketplace_evals.usage import Usage

RUNTIME = "claude-code"

TOOL_MAP: ToolMap = {
    "Read": (READ_FILE, {"path": "file_path"}),
    "Grep": (SEARCH, {"pattern": "pattern", "path": "path"}),
    "Glob": (FIND_FILES, {"pattern": "pattern", "path": "path"}),
    "Bash": (SHELL, {"command": "command"}),
    "Edit": (EDIT_FILE, {"path": "file_path"}),
    "MultiEdit": (EDIT_FILE, {"path": "file_path"}),
    "NotebookEdit": (EDIT_FILE, {"path": "notebook_path"}),
    "Write": (WRITE_FILE, {"path": "file_path"}),
    "Skill": (LOAD_SKILL, {"name": "skill"}),
    "Agent": (DELEGATE, {"agent": "subagent_type"}),
    "Task": (DELEGATE, {"agent": "subagent_type"}),  # old name of Agent
}

# The tool result of a background delegation, which only says it started.
_ASYNC_LAUNCH = "Async agent launched"


def parse_stream_json(lines: list[str], sandbox: Sandbox, duration_s: float = 0.0) -> Trace:
    """The trace of a `claude -p --output-format stream-json` session."""
    events = [json.loads(line) for line in lines if line.strip()]
    failed_ids = {
        block["tool_use_id"]
        for event in events
        if event.get("type") == "user"
        for block in _content(event)
        if block.get("type") == "tool_result" and block.get("is_error")
    }

    trace = Trace(runtime=RUNTIME)
    # A subagent's tool calls carry parent_tool_use_id = id of the delegation.
    delegations: dict[str, str] = {}
    # A reply is streamed as several assistant events sharing one message id.
    replies: dict[str, set[str]] = {}
    for event in events:
        match event.get("type"), event.get("subtype"):
            case "assistant", _:
                parent = event.get("parent_tool_use_id")
                by = MAIN_AGENT if parent is None else delegations.get(parent, f"?{parent}")
                message = event.get("message", {})
                if model := message.get("model"):
                    trace.models.setdefault(by, set()).add(model)
                if message_id := message.get("id"):
                    replies.setdefault(by, set()).add(message_id)
                for block in _content(event):
                    if block.get("type") != "tool_use":
                        continue
                    call = to_canonical(
                        block["name"],
                        block.get("input", {}),
                        TOOL_MAP,
                        sandbox,
                        block["id"] in failed_ids,
                        by,
                    )
                    if call.action == DELEGATE:
                        delegations[block["id"]] = call.args.get("agent", "?")
                    trace.calls.append(call)
            case "user", _:
                # Synchronous delegation: the report arrives as the tool result.
                for block in _content(event):
                    agent = delegations.get(block.get("tool_use_id", ""))
                    text = _text(block.get("content")) if block.get("type") == "tool_result" else ""
                    if agent and text and not text.startswith(_ASYNC_LAUNCH):
                        trace.reports[agent] = sandbox.normalize(text)
            case "system", "task_notification":
                # Background delegation: the report arrives in the final notification.
                agent = delegations.get(event.get("tool_use_id", ""))
                if agent and event.get("summary"):
                    trace.reports[agent] = sandbox.normalize(event["summary"])
            case "result", _:
                # With background subagents there is one result per turn; the last one
                # has the final answer and the cumulative usage.
                trace.final_output = event.get("result", "")
                trace.usage = usage_from_result(event, duration_s)
    trace.turns = {agent: len(ids) for agent, ids in replies.items()}
    return trace


def usage_from_result(result: dict, duration_s: float) -> Usage:
    """Usage of a whole `claude -p` session, subagents included.

    `modelUsage` is cumulative per model for the session; the `usage` field only
    covers the last turn, so it is not used.
    """
    usage = Usage(duration_s=duration_s, calls=1)
    for model, m in (result.get("modelUsage") or {}).items():
        usage.input_tokens += m.get("inputTokens", 0)
        usage.output_tokens += m.get("outputTokens", 0)
        usage.cache_read_tokens += m.get("cacheReadInputTokens", 0)
        usage.cache_write_tokens += m.get("cacheCreationInputTokens", 0)
        usage.cost_usd += m.get("costUSD", 0.0)
        usage.models.add(model)
    return usage


def _text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict))
    return ""


def _content(event: dict) -> list[dict]:
    content = event.get("message", {}).get("content", [])
    return content if isinstance(content, list) else []
