"""Reads Copilot CLI's output, shared by the runner and judge.

`--output-format json` gives the session's events (tool calls, messages, models) but
no tokens; those come from the OpenTelemetry file export of the same session.
"""

import json
import re

from marketplace_evals.runtimes.canonical import ToolMap, to_canonical
from marketplace_evals.runtimes.process import excerpt
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
    ToolCall,
    Trace,
)
from marketplace_evals.usage import Usage

RUNTIME = "copilot"

# `apply_patch` has no arguments of its own: see _patch_calls.
TOOL_MAP: ToolMap = {
    "view": (READ_FILE, {"path": "path"}),
    "rg": (SEARCH, {"pattern": "pattern", "path": "paths"}),
    "grep": (SEARCH, {"pattern": "pattern", "path": "paths"}),
    "glob": (FIND_FILES, {"pattern": "pattern", "path": "paths"}),
    "bash": (SHELL, {"command": "command"}),
    "edit": (EDIT_FILE, {"path": "path"}),
    "create": (WRITE_FILE, {"path": "path"}),
    "skill": (LOAD_SKILL, {"name": "skill"}),
    "task": (DELEGATE, {"agent": "agent_type"}),
}

NANO_AIU = 1_000_000_000

_PATCH_FILE = re.compile(r"^\*\*\* (Add|Update|Delete) File: (.+)$", re.MULTILINE)


def read_jsonl(text: str) -> list[dict]:
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def failure(events: list[dict], stderr: str) -> str | None:
    """Why the session failed, if it did. The CLI exits 0 even when it cannot start."""
    result = next((e for e in reversed(events) if e.get("type") == "result"), None)
    if result is None:
        return excerpt(stderr) or "no result event in the output"
    if result.get("exitCode"):
        # The CLI says why in a session.error event, not on stderr.
        errors = [e["data"].get("message", "") for e in events if e.get("type") == "session.error"]
        return f"exit code {result['exitCode']}: {excerpt('; '.join(filter(None, errors)) or stderr)}"
    return None


def final_message(events: list[dict]) -> str:
    messages = [e["data"].get("content", "") for e in events if e.get("type") == "assistant.message"]
    return next((m for m in reversed(messages) if m), "")


def parse_events(events: list[dict], sandbox: Sandbox) -> Trace:
    """The trace of a `copilot -p --output-format json` session, without its usage."""
    trace = Trace(runtime=RUNTIME)
    finished = {e["data"]["toolCallId"]: e["data"] for e in events if e.get("type") == "tool.execution_complete"}
    turns = 0
    for event in events:
        data = event.get("data", {})
        match event.get("type"):
            case "assistant.turn_start":
                turns += 1
            case "assistant.message" if data.get("model"):
                trace.models.setdefault(MAIN_AGENT, set()).add(data["model"])
            case "tool.execution_start":
                # Denied by permissions or failed: the runtime reports both as success=false.
                failed = finished.get(data["toolCallId"], {}).get("success") is False
                trace.calls += _to_canonical(data["toolName"], data.get("arguments"), sandbox, failed)
    if turns:
        trace.turns = {MAIN_AGENT: turns}
    trace.final_output = final_message(events)
    return trace


def usage_from_session(events: list[dict], otel_rows: list[dict], duration_s: float) -> Usage:
    """Usage of a whole session: tokens and models per model call, AI credits in total."""
    usage = Usage(duration_s=duration_s, calls=1)
    for row in otel_rows:
        if row.get("type") != "span" or not row.get("name", "").startswith("chat"):
            continue
        attributes = row.get("attributes", {})
        cache_read = _tokens(attributes, "cache_read.input_tokens")
        cache_write = _tokens(attributes, "cache_creation.input_tokens")
        # OTel GenAI conventions count the cached tokens inside input_tokens.
        usage.input_tokens += _tokens(attributes, "input_tokens") - cache_read - cache_write
        usage.output_tokens += _tokens(attributes, "output_tokens")
        usage.cache_read_tokens += cache_read
        usage.cache_write_tokens += cache_write
        # Not inside output_tokens: Vertex reports them apart, and bills them as output.
        usage.reasoning_tokens += _tokens(attributes, "reasoning.output_tokens")
        if model := attributes.get("gen_ai.response.model"):
            usage.models.add(model)
    usage.models |= {
        e["data"]["model"] for e in events if e.get("type") == "assistant.message" and e["data"].get("model")
    }
    checkpoints = [e for e in events if e.get("type") == "session.usage_checkpoint"]
    if checkpoints:  # cumulative: the last one covers the whole session
        usage.ai_credits = checkpoints[-1]["data"].get("totalNanoAiu", 0) / NANO_AIU
    return usage


def _tokens(attributes: dict, name: str) -> int:
    """A token count of an OTel GenAI span (`gen_ai.usage.<name>`)."""
    return int(attributes.get(f"gen_ai.usage.{name}") or 0)


def _to_canonical(name: str, raw_args: object, sandbox: Sandbox, failed: bool) -> list[ToolCall]:
    if name == "apply_patch":
        return _patch_calls(str(raw_args), sandbox, failed)
    return [to_canonical(name, raw_args if isinstance(raw_args, dict) else {}, TOOL_MAP, sandbox, failed)]


def _patch_calls(patch: str, sandbox: Sandbox, failed: bool) -> list[ToolCall]:
    """One call per file the patch touches: a new file is a write, the rest are edits."""
    return [
        ToolCall(
            WRITE_FILE if operation == "Add" else EDIT_FILE,
            {"path": sandbox.normalize(path.strip()).removeprefix("./")},
            "apply_patch",
            failed,
        )
        for operation, path in _PATCH_FILE.findall(patch)
    ]
