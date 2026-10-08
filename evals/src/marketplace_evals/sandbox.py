"""Sandbox: everything one run touches, inside a disposable folder.

    <root>/
    ├── workspace/            the agent's working directory, made from the fixture
    ├── plugins/<plugin>/     a copy of the plugin, so the agent cannot change the real one
    │                         (without the skill under evaluation, in a baseline run)
    ├── bin/                  first in the PATH, for stubs a fixture installs (`gh`...)
    └── .gitconfig            git's global configuration, instead of the user's

`EVAL_SANDBOX` points at the root, for setup scripts and stubs.
"""

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from marketplace_evals.goldens import GoldenCheck
from marketplace_evals.trace import PLUGINS_PREFIX, SANDBOX_PREFIX, CheckRun

# A fixture's setup script: run in the workspace before the agent, never copied into it.
SETUP_SCRIPT = "setup.sh"
SETUP_TIMEOUT_S = 120
INSPECT_TIMEOUT_S = 60
CHECK_TIMEOUT_S = 60
# How `before` commands and checks run: a check of several lines fails at the first
# that fails, not only by the last one, and a pipeline by any of its commands.
SHELL = ["bash", "-e", "-o", "pipefail", "-c"]
CHECK_OUTPUT_CHARS = 500  # of a check's output, kept to see why it failed

# Never part of the agent's work as files: git's own database. What the agent did with
# git is shown to the judge through the golden's `inspect` commands instead.
IGNORED_DIRS = {".git"}

# Isolates git from the user's own configuration (hooks, signing, aliases, identity).
GITCONFIG = """\
[user]
\tname = Eval Agent
\temail = eval-agent@example.com
[init]
\tdefaultBranch = main
[commit]
\tgpgsign = false
[tag]
\tgpgsign = false
"""


class SetupError(RuntimeError):
    """A fixture's setup script or a golden's `before` command failed: the run cannot start."""


@dataclass(frozen=True)
class Sandbox:
    root: Path
    plugin: str  # name of the plugin under evaluation

    @property
    def workspace(self) -> Path:
        return self.root / "workspace"

    @property
    def plugins(self) -> Path:
        return self.root / "plugins"

    @property
    def plugin_dir(self) -> Path:
        return self.plugins / self.plugin

    @property
    def bin(self) -> Path:
        return self.root / "bin"

    @classmethod
    def create(cls, root: Path, fixture_dir: Path, plugin_dir: Path, without_skill: str | None = None) -> Sandbox:
        """The fixture as the workspace, a copy of the plugin, and the fixture's setup
        script run in the workspace. `fixture_dir` and `plugin_dir` are left untouched.
        With `without_skill`, that skill is removed from the copy: the rest of the plugin
        stays loaded, as a user without the skill would have it."""
        sandbox = cls(root, plugin_dir.name)
        shutil.copytree(fixture_dir, sandbox.workspace, ignore=shutil.ignore_patterns(SETUP_SCRIPT))
        shutil.copytree(plugin_dir, sandbox.plugin_dir)
        if without_skill is not None:
            skill_dir = sandbox.plugin_dir / "skills" / without_skill
            if not skill_dir.is_dir():
                raise SetupError(f"{plugin_dir} has no skill {without_skill!r} to leave out")
            shutil.rmtree(skill_dir)
        sandbox.bin.mkdir()
        (root / ".gitconfig").write_text(GITCONFIG)
        if (setup := fixture_dir / SETUP_SCRIPT).is_file():
            proc = sandbox.run(["bash", str(setup.resolve())], timeout=SETUP_TIMEOUT_S)
            if proc.returncode != 0:
                output = (proc.stderr or proc.stdout).strip()[:500]
                raise SetupError(f"{setup} exited with code {proc.returncode}: {output}")
        return sandbox

    def env(self, base: dict[str, str] | None = None) -> dict[str, str]:
        """`base` (by default, this process's environment) with the sandbox's own PATH
        and git configuration."""
        env = dict(os.environ if base is None else base)
        env["PATH"] = f"{self.bin}{os.pathsep}{env.get('PATH', '')}"
        env["GIT_CONFIG_GLOBAL"] = str(self.root / ".gitconfig")
        env["GIT_CONFIG_NOSYSTEM"] = "1"
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["EVAL_SANDBOX"] = str(self.root)
        return env

    def run(self, command: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
        """Run `command` in the workspace, with the sandbox's environment."""
        return subprocess.run(
            command,
            cwd=self.workspace,
            env=self.env(),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def prepare(self, commands: tuple[str, ...]) -> None:
        """Run the golden's `before` commands, which record state for the checks."""
        for command in commands:
            proc = self.run([*SHELL, command], timeout=SETUP_TIMEOUT_S)
            if proc.returncode != 0:
                output = self.normalize((proc.stderr or proc.stdout).strip())[:500]
                raise SetupError(f"before command {command!r} exited with code {proc.returncode}: {output}")

    def run_checks(self, checks: tuple[GoldenCheck, ...]) -> dict[str, CheckRun]:
        """Run each check in the workspace. A check that times out does not pass."""
        runs = {}
        for check in checks:
            try:
                proc = self.run([*SHELL, check.run], timeout=CHECK_TIMEOUT_S)
                exit_code, output = proc.returncode, (proc.stdout + proc.stderr).strip()
            except subprocess.TimeoutExpired:
                exit_code, output = -1, f"timed out after {CHECK_TIMEOUT_S}s"
            runs[check.name] = CheckRun(check.name, exit_code, self.normalize(output)[:CHECK_OUTPUT_CHARS])
        return runs

    def inspect(self, commands: tuple[str, ...]) -> str:
        """The output of each command, as `$ command` followed by what it printed."""
        blocks = []
        for command in commands:
            proc = self.run(["bash", "-c", command], timeout=INSPECT_TIMEOUT_S)
            output = self.normalize((proc.stdout + proc.stderr).rstrip())
            blocks.append(f"$ {command}\n{output}" if output else f"$ {command}")
        return "\n\n".join(blocks)

    def snapshot(self) -> dict[str, str]:
        """Text files of the workspace, by relative path, without git's database."""
        files = {}
        for path in sorted(self.workspace.rglob("*")):
            rel = path.relative_to(self.workspace)
            if not path.is_file() or IGNORED_DIRS & set(rel.parts):
                continue
            try:
                files[rel.as_posix()] = path.read_text()
            except UnicodeDecodeError:
                continue
        return files

    def normalize(self, text: str) -> str:
        """Paths relative to the workspace, the plugin's copy as `@plugins/`, and anything
        else in the sandbox (a fixture's remote, say) as `@sandbox/`."""
        # macOS: /tmp links to /private/tmp and the runtime may use either. Longest
        # first, so /tmp/x is not replaced inside /private/tmp/x.
        replacements = [
            (str(path), prefix)
            for folder, prefix in (
                (self.workspace, None),
                (self.plugins, PLUGINS_PREFIX),
                (self.root, SANDBOX_PREFIX),
            )
            for path in {folder.resolve(), folder}
        ]
        for path, prefix in sorted(replacements, key=lambda r: len(r[0]), reverse=True):
            if prefix is None:
                text = text.replace(path + "/", "").replace(path, ".")
            else:
                text = text.replace(path + "/", prefix).replace(path, prefix.rstrip("/"))
        return text


def changed_files(final: dict[str, str], initial: dict[str, str]) -> list[str]:
    """Files in `final` that are new or differ from `initial`."""
    return [path for path, text in final.items() if initial.get(path) != text]
