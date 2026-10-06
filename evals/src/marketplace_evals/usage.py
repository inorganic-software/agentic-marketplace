"""Token, cost and time usage, in a runtime-independent shape.

Runners attach one Usage to each trace; judges accumulate theirs across calls. The
cost is the runtime's list-price estimate: with a subscription it is not what is
actually billed. Copilot does not price in dollars: it reports AI credits instead,
and none with a BYOK provider such as Vertex, which only reports tokens.
"""

from dataclasses import dataclass, field, fields


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0
    duration_s: float = 0.0  # wall-clock time of the call(s), summed
    calls: int = 0
    models: set[str] = field(default_factory=set)
    ai_credits: float = 0.0  # Copilot only
    reasoning_tokens: int = 0  # only when the runtime reports them apart from output_tokens

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.reasoning_tokens
            + self.cache_read_tokens
            + self.cache_write_tokens
        )

    def add(self, other: Usage) -> None:
        for f in fields(self):
            if f.name == "models":
                self.models |= other.models
            else:
                setattr(self, f.name, getattr(self, f.name) + getattr(other, f.name))


def total(usages: list[Usage]) -> Usage:
    result = Usage()
    for usage in usages:
        result.add(usage)
    return result


def model_mismatch(used: set[str], requested: str) -> str | None:
    """Error message if the runtime used any model other than the requested one."""
    if used and used != {requested}:
        return f"model mismatch: requested {requested!r}, runtime used {sorted(used)}"
    return None
