"""Translation of a runtime's tool calls into the canonical vocabulary (trace.py)."""

from marketplace_evals.sandbox import Sandbox
from marketplace_evals.trace import MAIN_AGENT, ToolCall

# Runtime tool -> (canonical action, {canonical arg: runtime arg})
ToolMap = dict[str, tuple[str, dict[str, str]]]


def to_canonical(
    name: str,
    raw_args: dict,
    tool_map: ToolMap,
    sandbox: Sandbox,
    denied: bool,
    by: str = MAIN_AGENT,
) -> ToolCall:
    """A runtime's tool call in the canonical vocabulary, with its paths normalized.
    A tool missing from `tool_map` becomes `unknown:<name>`, with its arguments as they are."""
    if name not in tool_map:
        return ToolCall(f"unknown:{name}", {k: str(v) for k, v in raw_args.items()}, name, denied, by)
    action, arg_map = tool_map[name]
    args = {
        canonical: sandbox.normalize(_as_text(raw_args[original]))
        for canonical, original in arg_map.items()
        if original in raw_args
    }
    if "path" in args:  # `./src/x` and `src/x` are the same file
        args["path"] = args["path"].removeprefix("./")
    if "name" in args:  # `commons:git-workflow` (Claude Code) is `git-workflow` (Copilot)
        args["name"] = args["name"].rsplit(":", 1)[-1]
    return ToolCall(action, args, name, denied, by)


def _as_text(value: object) -> str:
    """An argument as text: a list (for example, several search paths) joined by spaces."""
    return " ".join(map(str, value)) if isinstance(value, list) else str(value)
