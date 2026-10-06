import json
import subprocess
import tempfile
from pathlib import Path

from marketplace_evals.runtimes.copilot.environment import isolated_env
from marketplace_evals.runtimes.copilot.output import failure, final_message, read_jsonl, usage_from_session
from marketplace_evals.runtimes.copilot.vertex import Vertex
from marketplace_evals.runtimes.judge import DEFAULT_TIMEOUT_S, SYSTEM_PROMPT, Judge, JudgeError
from marketplace_evals.runtimes.process import excerpt, run_cli
from marketplace_evals.usage import Usage, model_mismatch


class CopilotCliJudge(Judge):
    """Judge backed by headless Copilot CLI, with the models of the local Copilot login
    or, when `vertex` is given, of Vertex AI.

    Isolated so it only reads the prompt: no tools, no custom instructions, empty
    COPILOT_HOME, run from an empty folder. Copilot CLI has no structured output, so
    the schema goes in the prompt and the answer must parse as JSON.
    """

    name = "copilot-cli"

    def __init__(self, model: str, timeout_s: float = DEFAULT_TIMEOUT_S, vertex: Vertex | None = None):
        super().__init__(model, timeout_s)
        self.vertex = vertex

    def ask(self, prompt: str, json_schema: dict | None = None) -> str:
        command = [
            "copilot",
            "--model", self.model,
            "--output-format", "json",
            # The only tool left is ask_user, which --no-ask-user removes: no tools at all.
            "--available-tools", "ask_user", "--no-ask-user",
            "--no-custom-instructions",
            "--disable-builtin-mcps",
            "--no-auto-update",
        ]  # fmt: skip
        with tempfile.TemporaryDirectory(prefix="judge-") as cwd:
            home = Path(cwd) / ".copilot-home"
            home.mkdir()
            otel_file = home / "otel.jsonl"
            env = isolated_env(home, otel_file, self.model, self.vertex)
            try:
                done = run_cli(
                    command,
                    input=full_prompt(prompt, json_schema),
                    timeout=self.timeout_s,
                    cwd=cwd,
                    env=env,
                )
            except subprocess.TimeoutExpired as e:
                self.record(Usage(duration_s=self.timeout_s, calls=1))
                raise JudgeError(f"timed out after {self.timeout_s}s") from e
            otel_rows = read_jsonl(otel_file.read_text()) if otel_file.exists() else []
        try:
            events = read_jsonl(done.proc.stdout)
        except json.JSONDecodeError as e:
            self.record(Usage(duration_s=done.duration_s, calls=1))
            raise JudgeError(f"output is not JSONL: {excerpt(done.proc.stdout)}") from e
        usage = usage_from_session(events, otel_rows, done.duration_s)
        self.record(usage)
        if error := failure(events, done.proc.stderr):
            raise JudgeError(f"copilot failed: {error}")
        if error := model_mismatch(usage.models, self.model):
            raise JudgeError(error)
        answer = final_message(events)
        if json_schema is None:
            return answer
        try:
            return json.dumps(json.loads(strip_fences(answer)))
        except json.JSONDecodeError as e:
            raise JudgeError(f"answer is not JSON: {excerpt(answer)}") from e


def full_prompt(prompt: str, json_schema: dict | None) -> str:
    """The system prompt, the question and, if any, the schema the answer must satisfy."""
    parts = [SYSTEM_PROMPT, prompt]
    if json_schema is not None:
        parts.append(
            "Answer only with a JSON object that satisfies this JSON Schema, "
            f"with no other text:\n{json.dumps(json_schema)}"
        )
    return "\n\n".join(parts)


def strip_fences(text: str) -> str:
    """The JSON alone, without the ```json fence some models wrap it in."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        text = text.rsplit("```", 1)[0]
    return text.strip()
