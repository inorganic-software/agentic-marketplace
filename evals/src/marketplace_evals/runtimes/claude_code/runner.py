from marketplace_evals.runtimes.claude_code.output import RUNTIME, parse_stream_json
from marketplace_evals.runtimes.process import excerpt, run_cli
from marketplace_evals.runtimes.runner import AgentTask, Runner
from marketplace_evals.sandbox import Sandbox
from marketplace_evals.trace import Trace

# Golden tool -> Claude Code tools. `Skill` is always allowed: loading the skill is
# what is being evaluated.
TOOLS: dict[str, list[str]] = {
    "read": ["Read"],
    "edit": ["Edit", "Write"],
    "search": ["Grep", "Glob"],
    "execute": ["Bash"],
}


class ClaudeCodeRunner(Runner):
    """Adapter for Claude Code (`claude -p`), run locally, with the plugin loaded
    through `--plugin-dir`."""

    name = RUNTIME
    cli = "claude"

    def _launch(self, task: AgentTask, sandbox: Sandbox) -> Trace:
        command = [
            self.cli, "-p", task.prompt,
            "--plugin-dir", str(sandbox.plugin_dir),
            "--model", self.model,
            "--output-format", "stream-json", "--verbose",
            # Isolation: no user settings/hooks/plugins and no user MCPs.
            "--setting-sources", "project",
            "--strict-mcp-config",
            "--no-session-persistence",
            # Only the golden's tools run; anything else is denied without asking,
            # but the attempt stays in the trace.
            "--permission-mode", "dontAsk",
            "--allowedTools", " ".join(allowed_tools(task.tools)),
        ]  # fmt: skip
        done = run_cli(command, timeout=self.timeout_s, cwd=sandbox.workspace, env=sandbox.env())
        raw_log = self._save_log(sandbox, done.proc.stdout)
        if done.proc.returncode != 0:
            raise RuntimeError(f"claude exited with code {done.proc.returncode}: {excerpt(done.proc.stderr)}")
        trace = parse_stream_json(done.proc.stdout.splitlines(), sandbox, done.duration_s)
        trace.raw_log = raw_log
        return trace


def allowed_tools(tools: tuple[str, ...]) -> list[str]:
    """The Claude Code tools a golden's tools grant, plus Skill."""
    allowed: list[str] = []
    for tool in tools:
        allowed += [t for t in TOOLS[tool] if t not in allowed]
    return [*allowed, "Skill"]
