"""Matchers: which tool calls a golden expects or forbids.

`match` compares each argument against a glob pattern (fnmatch). `not_match` excludes
calls whose argument matches any of its globs. A matcher without either accepts any
call to that action. `by` limits the matcher to the calls of one agent ("main" is
the main session); without `by` any agent counts.

A shell call's `command` is compared one simple command at a time: `a && b; c | d` is
`a`, `b`, `c` and `d`, split outside quotes, and the call matches if one of them meets
`match` and none of `not_match`. Otherwise a `*` would reach from one command into the
next: `*git push*main*` would match `git push -u origin feat/x && gh pr create --base main`.
"""

from dataclasses import dataclass, field
from fnmatch import fnmatchcase

from marketplace_evals.trace import SHELL, ToolCall

# What separates the simple commands of a shell command line.
SEPARATORS = ("&&", "||", ";", "|", "\n")


@dataclass(frozen=True)
class CallMatcher:
    action: str
    match: dict[str, str] = field(default_factory=dict)
    by: str | None = None
    not_match: dict[str, list[str]] = field(default_factory=dict)

    def matches(self, call: ToolCall) -> bool:
        if call.action != self.action or (self.by is not None and call.by != self.by):
            return False
        if call.action == SHELL and "command" in call.args:
            commands = simple_commands(call.args["command"])
            return any(self._matches_args(call.args | {"command": c}) for c in commands)
        return self._matches_args(call.args)

    def _matches_args(self, args: dict[str, str]) -> bool:
        return all(fnmatchcase(args.get(arg, ""), pattern) for arg, pattern in self.match.items()) and not any(
            fnmatchcase(args.get(arg, ""), pattern) for arg, patterns in self.not_match.items() for pattern in patterns
        )

    def __str__(self) -> str:
        args = [f"{k}~{v!r}" for k, v in self.match.items()]
        args += [f"{k}!~{v!r}" for k, v in self.not_match.items()]
        return f"{self.by or '*'}: {self.action}({', '.join(args)})"


@dataclass(frozen=True)
class AnyOf:
    """Passes if any of its matchers matches."""

    matchers: list[CallMatcher]

    def matches(self, call: ToolCall) -> bool:
        return any(m.matches(call) for m in self.matchers)

    def __str__(self) -> str:
        return " or ".join(str(m) for m in self.matchers)


Expectation = CallMatcher | AnyOf


def simple_commands(line: str) -> list[str]:
    """The simple commands of a shell command line, split at SEPARATORS outside quotes.
    A best effort, not a shell parser: enough that a glob does not span two commands."""
    commands, current, quote, i = [], [], None, 0
    while i < len(line):
        char = line[i]
        if quote:
            if char == "\\" and quote == '"' and i + 1 < len(line):
                current.append(line[i : i + 2])
                i += 2
                continue
            if char == quote:
                quote = None
        elif char in "'\"":
            quote = char
        elif sep := next((s for s in SEPARATORS if line.startswith(s, i)), None):
            commands.append("".join(current))
            current = []
            i += len(sep)
            continue
        current.append(char)
        i += 1
    commands.append("".join(current))
    return [c.strip() for c in commands if c.strip()]
