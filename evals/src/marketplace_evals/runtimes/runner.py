"""The agent's side of a runtime: a session with the plugin under evaluation loaded."""

import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from marketplace_evals.goldens import GoldenCheck
from marketplace_evals.sandbox import Sandbox, changed_files
from marketplace_evals.trace import Trace

DEFAULT_TIMEOUT_S = 900


@dataclass(frozen=True)
class AgentTask:
    """A run to launch: a session with only the plugin under evaluation loaded."""

    prompt: str
    plugin_dir: Path  # the plugin, as published in plugins/<plugin>/
    fixture_dir: Path  # what the agent will see as its repo, plus an optional setup.sh
    tools: tuple[str, ...]  # what the agent may use, in the goldens' vocabulary
    inspect: tuple[str, ...] = ()  # commands whose output the judge sees
    before: tuple[str, ...] = ()  # commands that record state for the checks, before the agent
    checks: tuple[GoldenCheck, ...] = ()  # run after the agent, before the sandbox is deleted
    without_skill: str | None = None  # a baseline run: this skill is left out of the plugin


class Runner(ABC):
    """Adapter for an agent runtime.

    Each implementation knows how to load the plugin in its runtime, launch the
    session headless and translate its trace into the canonical vocabulary. What
    they share (the sandbox, the state before and after, the logs) is here.
    """

    name: str
    cli: str  # executable of the runtime

    def __init__(self, model: str, timeout_s: float = DEFAULT_TIMEOUT_S, logs_dir: Path | None = None):
        self.model = model  # exact model ID; every agent in the trace must have used it
        self.timeout_s = timeout_s
        self.logs_dir = logs_dir

    def version(self) -> str | None:
        """Version of the runtime, recorded with the results."""
        out = subprocess.run([self.cli, "--version"], capture_output=True, text=True).stdout.strip()
        return out.splitlines()[0] if out else None

    def run(self, task: AgentTask, root: Path) -> Trace:
        """Run the task in `root` (empty and disposable) and return the trace."""
        sandbox = Sandbox.create(root, task.fixture_dir, task.plugin_dir, task.without_skill)
        sandbox.prepare(task.before)
        initial_files = sandbox.snapshot()
        initial_state = sandbox.inspect(task.inspect)
        trace = self._launch(task, sandbox)
        trace.prompt = task.prompt
        trace.final_files = sandbox.snapshot()
        trace.changed_files = changed_files(trace.final_files, initial_files)
        trace.initial_state = initial_state
        trace.final_state = sandbox.inspect(task.inspect)
        trace.check_runs = sandbox.run_checks(task.checks)
        self._keep(trace, sandbox)
        return trace

    @abstractmethod
    def _launch(self, task: AgentTask, sandbox: Sandbox) -> Trace:
        """Run the session in the prepared sandbox and parse its trace, with `raw_log`."""

    def _save_log(self, sandbox: Sandbox, output: str) -> str | None:
        """Keep the runtime's raw output; returns where, if anywhere."""
        if self.logs_dir is None:
            return None
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        path = self.logs_dir / f"{sandbox.root.name}.jsonl"
        path.write_text(output)
        return str(path)

    def _keep(self, trace: Trace, sandbox: Sandbox) -> None:
        """Keep the final files and the state next to the log: the sandbox is deleted."""
        if self.logs_dir is None:
            return
        kept = self.logs_dir / sandbox.root.name
        for rel, text in trace.final_files.items():
            path = kept / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        if trace.final_state or trace.check_runs:
            self.logs_dir.mkdir(parents=True, exist_ok=True)
            (self.logs_dir / f"{sandbox.root.name}.state.txt").write_text(
                f"== before\n{trace.initial_state}\n\n== after\n{trace.final_state}\n" + _checks_text(trace)
            )


def _checks_text(trace: Trace) -> str:
    """Each check's result, with the output of those that failed."""
    if not trace.check_runs:
        return ""
    lines = ["", "== checks"]
    for run in trace.check_runs.values():
        lines.append(f"[{'x' if run.passed else ' '}] {run.name}")
        if not run.passed:
            lines.append(f"    exit {run.exit_code}" + (f": {run.output}" if run.output else ""))
    return "\n".join(lines) + "\n"
