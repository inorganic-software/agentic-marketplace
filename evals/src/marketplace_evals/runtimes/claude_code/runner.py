import json
import os
from pathlib import Path

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


# What Claude Code may need to log in, if this process has it: an API key or a token
# (`claude setup-token`), or another provider. A login of `claude /login` lives in the
# keychain instead, and LOGIN_KEYS of ~/.claude.json say which one to use.
LOGIN_VARS = {"CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLOUD_ML_REGION"}
LOGIN_PREFIXES = ("ANTHROPIC_",)
LOGIN_KEYS = ("oauthAccount", "userID", "hasCompletedOnboarding")


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
        write_login(sandbox.home)
        done = run_cli(command, timeout=self.timeout_s, cwd=sandbox.workspace, env=sandbox.agent_env(login_env()))
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


def login_env() -> dict[str, str]:
    """The variables of this process that log Claude Code in, if any."""
    return {k: v for k, v in os.environ.items() if k in LOGIN_VARS or k.startswith(LOGIN_PREFIXES)}


def write_login(home: Path) -> None:
    """The agent's ~/.claude.json, with only what says which login to use: without it,
    Claude Code in a new HOME is not logged in."""
    user_config = Path.home() / ".claude.json"
    config = json.loads(user_config.read_text()) if user_config.is_file() else {}
    (home / ".claude.json").write_text(json.dumps({k: config[k] for k in LOGIN_KEYS if k in config}))
