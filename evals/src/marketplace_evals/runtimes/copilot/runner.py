import tempfile
from pathlib import Path

from marketplace_evals.runtimes.copilot.environment import isolated_env
from marketplace_evals.runtimes.copilot.output import RUNTIME, failure, parse_events, read_jsonl, usage_from_session
from marketplace_evals.runtimes.copilot.vertex import Vertex
from marketplace_evals.runtimes.process import run_cli
from marketplace_evals.runtimes.runner import DEFAULT_TIMEOUT_S, AgentTask, Runner
from marketplace_evals.sandbox import Sandbox
from marketplace_evals.trace import Trace

# Golden tool -> permission kind granted to it (`--allow-tool`). Anything not
# granted is denied without asking, but the attempt stays in the trace.
PERMISSIONS: dict[str, str] = {
    "read": "read",
    "search": "read",
    "edit": "write",
    "execute": "shell",
}


class CopilotRunner(Runner):
    """Adapter for GitHub Copilot CLI (`copilot -p`), run locally.

    Copilot reads the plugin natively (`--plugin-dir`), in the same Claude Code format.
    Every session gets an empty COPILOT_HOME, so nothing from the user's own
    configuration (agents, skills, instructions, plugins, MCPs) reaches it. The models
    come from the user's Copilot login, or from Vertex AI when `vertex` is given.

    Not verified yet: subagent calls are attributed to `main`, since no skill
    evaluated so far delegates.
    """

    name = RUNTIME
    cli = "copilot"

    def __init__(
        self,
        model: str,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        logs_dir: Path | None = None,
        vertex: Vertex | None = None,
    ):
        super().__init__(model, timeout_s, logs_dir)
        self.vertex = vertex

    def _launch(self, task: AgentTask, sandbox: Sandbox) -> Trace:
        command = [
            self.cli, "-p", task.prompt,
            "--plugin-dir", str(sandbox.plugin_dir),
            "--model", self.model,
            "--output-format", "json",
            "--no-ask-user",
            "--no-auto-update",
            # Isolation: no built-in GitHub MCP server.
            "--disable-builtin-mcps",
            "--allow-tool", ", ".join(permissions(task.tools)),
        ]  # fmt: skip
        with tempfile.TemporaryDirectory(prefix="copilot-home-") as home:
            otel_file = Path(home) / "otel.jsonl"
            env = sandbox.env(isolated_env(Path(home), otel_file, self.model, self.vertex))
            done = run_cli(command, timeout=self.timeout_s, cwd=sandbox.workspace, env=env)
            otel_rows = read_jsonl(otel_file.read_text()) if otel_file.exists() else []
        raw_log = self._save_log(sandbox, done.proc.stdout)
        events = read_jsonl(done.proc.stdout)
        if error := failure(events, done.proc.stderr):
            raise RuntimeError(f"copilot failed: {error}")
        trace = parse_events(events, sandbox)
        trace.usage = usage_from_session(events, otel_rows, done.duration_s)
        trace.raw_log = raw_log
        return trace


def permissions(tools: tuple[str, ...]) -> list[str]:
    """Permission kinds granted by a golden's tools."""
    return list(dict.fromkeys(PERMISSIONS[tool] for tool in tools))
