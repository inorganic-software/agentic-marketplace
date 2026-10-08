"""Running a runtime's CLI: shared by every runner and judge."""

import subprocess
import time
from dataclasses import dataclass

# How much of a CLI's output an error message carries.
EXCERPT_CHARS = 500


@dataclass(frozen=True)
class Completed:
    proc: subprocess.CompletedProcess[str]
    duration_s: float


def run_cli(command: list[str], *, timeout: float, input: str | None = None, **kwargs) -> Completed:
    """Run a CLI with text I/O, timed. Without `input`, stdin is closed, so a CLI that
    waits for an answer fails instead of hanging. `kwargs` go to subprocess.run (cwd, env)."""
    start = time.monotonic()
    proc = subprocess.run(
        command,
        input=input,
        stdin=subprocess.DEVNULL if input is None else None,
        capture_output=True,
        text=True,
        timeout=timeout,
        **kwargs,
    )
    return Completed(proc, time.monotonic() - start)


def excerpt(text: str, chars: int = EXCERPT_CHARS) -> str:
    """The start of a CLI's output, for an error message."""
    return text.strip()[:chars]
