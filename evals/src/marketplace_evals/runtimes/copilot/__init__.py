"""GitHub Copilot CLI: runs the agent (`copilot -p --plugin-dir`) and the judge, with
the models of the user's Copilot login or of Vertex AI."""

from marketplace_evals.runtimes.copilot.judge import CopilotCliJudge
from marketplace_evals.runtimes.copilot.runner import CopilotRunner
from marketplace_evals.runtimes.copilot.vertex import Vertex

__all__ = ["CopilotCliJudge", "CopilotRunner", "Vertex"]
