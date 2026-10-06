"""The runtimes the evals run on. A provider is one runtime doing both jobs: running
the agent and judging it."""

from dataclasses import dataclass

from marketplace_evals.runtimes.claude_code import ClaudeCliJudge, ClaudeCodeRunner
from marketplace_evals.runtimes.copilot import CopilotCliJudge, CopilotRunner
from marketplace_evals.runtimes.judge import Judge, JudgeError
from marketplace_evals.runtimes.runner import AgentTask, Runner


@dataclass(frozen=True)
class Provider:
    runner: type[Runner]
    judge: type[Judge]


PROVIDERS: dict[str, Provider] = {
    "claude": Provider(ClaudeCodeRunner, ClaudeCliJudge),
    "copilot": Provider(CopilotRunner, CopilotCliJudge),
}

__all__ = ["PROVIDERS", "AgentTask", "Judge", "JudgeError", "Provider", "Runner"]
