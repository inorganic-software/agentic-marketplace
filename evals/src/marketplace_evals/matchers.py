"""Matchers: which tool calls a golden expects or forbids.

`match` compares each argument against a glob pattern (fnmatch). `not_match` excludes
calls whose argument matches any of its globs. A matcher without either accepts any
call to that action. `by` limits the matcher to the calls of one agent ("main" is
the main session); without `by` any agent counts.
"""

from dataclasses import dataclass, field
from fnmatch import fnmatchcase

from marketplace_evals.trace import ToolCall


@dataclass(frozen=True)
class CallMatcher:
    action: str
    match: dict[str, str] = field(default_factory=dict)
    by: str | None = None
    not_match: dict[str, list[str]] = field(default_factory=dict)

    def matches(self, call: ToolCall) -> bool:
        return (
            call.action == self.action
            and (self.by is None or call.by == self.by)
            and all(fnmatchcase(call.args.get(arg, ""), pattern) for arg, pattern in self.match.items())
            and not any(
                fnmatchcase(call.args.get(arg, ""), pattern)
                for arg, patterns in self.not_match.items()
                for pattern in patterns
            )
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
