import json
import subprocess
import tempfile

from marketplace_evals.runtimes.claude_code.output import usage_from_result
from marketplace_evals.runtimes.judge import SYSTEM_PROMPT, Judge, JudgeError
from marketplace_evals.runtimes.process import excerpt, run_cli
from marketplace_evals.usage import Usage, model_mismatch


class ClaudeCliJudge(Judge):
    """Judge backed by headless Claude Code (`claude -p`), using the local subscription.

    Isolated so it only reads the prompt: no tools, no user settings or MCPs, and
    run from an empty folder. With a schema, the answer is Claude Code's structured output.
    """

    name = "claude-cli"

    def ask(self, prompt: str, json_schema: dict | None = None) -> str:
        command = [
            "claude", "-p",
            "--model", self.model,
            "--tools", "",
            "--setting-sources", "project",
            "--strict-mcp-config",
            "--no-session-persistence",
            "--system-prompt", SYSTEM_PROMPT,
            "--output-format", "json",
        ]  # fmt: skip
        if json_schema is not None:
            command += ["--json-schema", json.dumps(json_schema)]
        with tempfile.TemporaryDirectory(prefix="judge-") as cwd:
            try:
                done = run_cli(command, input=prompt, timeout=self.timeout_s, cwd=cwd)
            except subprocess.TimeoutExpired as e:
                self.record(Usage(duration_s=self.timeout_s, calls=1))
                raise JudgeError(f"timed out after {self.timeout_s}s") from e
        proc = done.proc
        if proc.returncode != 0:
            self.record(Usage(duration_s=done.duration_s, calls=1))
            raise JudgeError(f"claude exited with code {proc.returncode}: {excerpt(proc.stderr)}")
        try:
            out = json.loads(proc.stdout)
        except json.JSONDecodeError as e:
            self.record(Usage(duration_s=done.duration_s, calls=1))
            raise JudgeError(f"output is not JSON: {excerpt(proc.stdout)}") from e
        usage = usage_from_result(out, done.duration_s)
        self.record(usage)
        if error := model_mismatch(usage.models, self.model):
            raise JudgeError(error)
        if out.get("is_error"):
            raise JudgeError(f"judge error: {excerpt(str(out.get('result')))}")
        if json_schema is None:
            return out.get("result", "")
        if out.get("structured_output") is None:
            raise JudgeError(f"no structured output: {excerpt(str(out.get('result')))}")
        return json.dumps(out["structured_output"])
