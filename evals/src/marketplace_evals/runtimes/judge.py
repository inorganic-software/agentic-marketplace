"""The LLM judge, independent of the runtime under evaluation."""

import threading
from abc import ABC, abstractmethod

from marketplace_evals.usage import Usage

SYSTEM_PROMPT = "You are an evaluator. Answer only what you are asked, with no extra commentary."
DEFAULT_TIMEOUT_S = 300


class JudgeError(Exception):
    """The judge could not give a valid answer (error, timeout, invalid JSON)."""


class Judge(ABC):
    """LLM that evaluates. Each runtime provides one, run with no tools from an empty
    folder, so it only reads the prompt."""

    name: str

    def __init__(self, model: str, timeout_s: float = DEFAULT_TIMEOUT_S):
        self.model = model  # exact model ID; every answer must come from it
        self.timeout_s = timeout_s
        self.usage = Usage()  # accumulated over every call, including failed ones
        self._usage_lock = threading.Lock()

    def record(self, usage: Usage) -> None:
        with self._usage_lock:  # metrics call the judge from several threads
            self.usage.add(usage)

    @abstractmethod
    def ask(self, prompt: str, json_schema: dict | None = None) -> str:
        """Return the answer as text. With `json_schema`, a JSON that satisfies it."""
