"""Canonical trace: what the agent did, in a runtime-independent vocabulary.

Each runtime (Claude Code, Copilot...) names its tools differently. The adapters
translate their tool calls into these actions, and goldens and metrics only speak
this vocabulary.
"""

from collections import Counter
from dataclasses import dataclass, field

from marketplace_evals.usage import Usage

# Canonical actions and their normalized arguments.
READ_FILE = "read_file"  # path
SEARCH = "search"  # pattern, path
FIND_FILES = "find_files"  # pattern, path
SHELL = "shell"  # command
EDIT_FILE = "edit_file"  # path
WRITE_FILE = "write_file"  # path
LOAD_SKILL = "load_skill"  # name, without the plugin prefix
DELEGATE = "delegate"  # agent: launches a subagent

ACTIONS = {READ_FILE, SEARCH, FIND_FILES, SHELL, EDIT_FILE, WRITE_FILE, LOAD_SKILL, DELEGATE}

# Author of a tool call that is not made by a subagent.
MAIN_AGENT = "main"

# Neutral prefix for the plugins under evaluation: each run loads its own copy from
# a temporary folder, so the adapter rewrites that folder to this prefix. A read of
# `@plugins/commons/skills/git-workflow/SKILL.md` is the same on every runtime.
PLUGINS_PREFIX = "@plugins/"
# Anything else in the run's sandbox outside the workspace, such as a fixture's remote.
SANDBOX_PREFIX = "@sandbox/"


@dataclass(frozen=True)
class ToolCall:
    action: str  # one of ACTIONS, or "unknown:<original name>"
    args: dict[str, str]
    raw_name: str  # tool name in the runtime, for diagnostics only
    denied: bool = False  # the runtime rejected it (permissions) or returned an error
    by: str = MAIN_AGENT  # agent that made it: MAIN_AGENT or the subagent's name

    def __str__(self) -> str:
        args = ", ".join(f"{k}={v!r}" for k, v in self.args.items())
        flag = " [denied/error]" if self.denied else ""
        return f"{self.by}: {self.action}({args}){flag}"


@dataclass
class Trace:
    runtime: str
    calls: list[ToolCall] = field(default_factory=list)
    final_output: str = ""
    models: dict[str, set[str]] = field(default_factory=dict)  # agent -> models used
    turns: dict[str, int] = field(default_factory=dict)  # agent -> model calls (one reply each)
    reports: dict[str, str] = field(default_factory=dict)  # subagent -> its final report
    # Final state of the workspace: relative path -> text, without git's database.
    final_files: dict[str, str] = field(default_factory=dict)
    changed_files: list[str] = field(default_factory=list)  # created or modified by the agent
    # Output of the golden's `inspect` commands before and after the agent (git state...).
    initial_state: str = ""
    final_state: str = ""
    raw_log: str | None = None  # path to the runtime's raw log, for debugging
    usage: Usage = field(default_factory=Usage)  # tokens, cost and time of the whole run

    @property
    def changed(self) -> bool:
        """The agent left something different: a file, or what `inspect` shows."""
        return bool(self.changed_files) or self.initial_state != self.final_state

    def tool_calls(self) -> dict[str, int]:
        """Agent -> number of tool calls it made."""
        return dict(Counter(call.by for call in self.calls))
