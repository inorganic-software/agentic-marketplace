"""Sandbox: everything one run touches, inside a disposable folder.

    <root>/
    ├── workspace/            the agent's working directory, made from the fixture
    ├── plugins/<plugin>/     a copy of the plugin, so the agent cannot change the real one
    │                         (without the skill under evaluation, in a baseline run)
    ├── bin/                  first in the PATH, for stubs a fixture installs (`gh`...)
    ├── home/                 the agent's HOME, with only what its runtime needs to log in
    ├── tmp/                  the agent's TMPDIR
    ├── stub-gaps.jsonl       calls those stubs do not imitate, one JSON object per line
    └── .gitconfig            git's global configuration, instead of the user's

The harness's own commands (the fixture's setup, the golden's `before`, `inspect` and
checks) run with this process's environment and `EVAL_SANDBOX` pointing at the root.
The agent gets a clean one instead (`agent_env`): only what its runtime needs, with no
trace of the eval, of the user's session or of the repo it runs from. The root has a
random name, and the run's own name (case, variant...) is only for its logs.
"""

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from marketplace_evals.goldens import GoldenCheck
from marketplace_evals.paths import REPO_DIR
from marketplace_evals.trace import PLUGINS_PREFIX, SANDBOX_PREFIX, CheckRun, StubGap

# A fixture's setup script: run in the workspace before the agent, never copied into it.
SETUP_SCRIPT = "setup.sh"
SETUP_TIMEOUT_S = 120
INSPECT_TIMEOUT_S = 60
CHECK_TIMEOUT_S = 60
# How `before` commands and checks run: a check of several lines fails at the first
# that fails, not only by the last one, and a pipeline by any of its commands.
SHELL = ["bash", "-e", "-o", "pipefail", "-c"]
CHECK_OUTPUT_CHARS = 500  # of a check's output, kept to see why it failed
# Where a stub appends each call it does not imitate, as
# {"stub": "gh", "argv": ["pr", "create", "--fill"], "reason": "unknown flag: --fill"}.
STUB_GAPS_FILE = "stub-gaps.jsonl"

# Never part of the agent's work as files: git's own database. What the agent did with
# git is shown to the judge through the golden's `inspect` commands instead.
IGNORED_DIRS = {".git"}

# What the agent's environment keeps from this process's: who the user is, the locale
# and the network settings. Everything else (the user's session, the repo's virtualenv,
# the eval's own variables, CI's credentials) stays out. A runtime adds what it needs
# to log in (`agent_env(extra)`).
AGENT_ENV_VARS = {
    "USER", "LOGNAME", "SHELL", "LANG", "TERM",
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "NODE_EXTRA_CA_CERTS",
}  # fmt: skip
AGENT_ENV_PREFIXES = ("LC_",)

# macOS keeps the runtimes' logins in the user's keychain, which it looks up from HOME:
# the agent's HOME links to it, or neither CLI finds its login.
KEYCHAINS = Path.home() / "Library" / "Keychains"

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
    name: str = ""  # of the run, for its logs; the root's own name says nothing of it

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

    @property
    def home(self) -> Path:
        return self.root / "home"

    @property
    def tmp(self) -> Path:
        return self.root / "tmp"

    @property
    def log_name(self) -> str:
        return self.name or self.root.name

    @classmethod
    def create(
        cls, root: Path, fixture_dir: Path, plugin_dir: Path, without_skill: str | None = None, name: str = ""
    ) -> Sandbox:
        """The fixture as the workspace, a copy of the plugin, and the fixture's setup
        script run in the workspace. `fixture_dir` and `plugin_dir` are left untouched.
        With `without_skill`, that skill is removed from the copy: the rest of the plugin
        stays loaded, as a user without the skill would have it."""
        sandbox = cls(root, plugin_dir.name, name)
        shutil.copytree(fixture_dir, sandbox.workspace, ignore=shutil.ignore_patterns(SETUP_SCRIPT))
        shutil.copytree(plugin_dir, sandbox.plugin_dir)
        if without_skill is not None:
            skill_dir = sandbox.plugin_dir / "skills" / without_skill
            if not skill_dir.is_dir():
                raise SetupError(f"{plugin_dir} has no skill {without_skill!r} to leave out")
            shutil.rmtree(skill_dir)
        sandbox.bin.mkdir()
        sandbox.tmp.mkdir()
        sandbox.home.mkdir()
        if KEYCHAINS.is_dir():
            (sandbox.home / "Library").mkdir()
            (sandbox.home / "Library" / "Keychains").symlink_to(KEYCHAINS)
        (root / ".gitconfig").write_text(GITCONFIG)
        if (setup := fixture_dir / SETUP_SCRIPT).is_file():
            proc = sandbox.run(["bash", str(setup.resolve())], timeout=SETUP_TIMEOUT_S)
            if proc.returncode != 0:
                output = (proc.stderr or proc.stdout).strip()[:500]
                raise SetupError(f"{setup} exited with code {proc.returncode}: {output}")
        return sandbox

    def env(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        """The environment of the harness's own commands: this process's, with the
        sandbox's PATH and git configuration, `EVAL_SANDBOX`, and `extra`."""
        env = dict(os.environ)
        env["PATH"] = f"{self.bin}{os.pathsep}{env.get('PATH', '')}"
        env |= self._git_env()
        env["EVAL_SANDBOX"] = str(self.root)
        return env | (extra or {})

    def agent_env(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        """The agent's environment, built from scratch: the sandbox's PATH (without the
        repo's own folders, such as its virtualenv), HOME and TMPDIR, git's isolated
        configuration, what AGENT_ENV_VARS keeps of this process's, and `extra`, what
        its runtime needs to log in."""
        env = {
            name: value
            for name, value in os.environ.items()
            if name in AGENT_ENV_VARS or name.startswith(AGENT_ENV_PREFIXES)
        }
        path = [p for p in os.environ.get("PATH", "").split(os.pathsep) if p and not _inside(Path(p), REPO_DIR)]
        env["PATH"] = os.pathsep.join([str(self.bin), *path])
        env["HOME"] = str(self.home)
        env["TMPDIR"] = str(self.tmp)
        env |= self._git_env()
        return env | (extra or {})

    def _git_env(self) -> dict[str, str]:
        return {
            "GIT_CONFIG_GLOBAL": str(self.root / ".gitconfig"),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
        }

    def run(
        self, command: list[str], timeout: float, extra_env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        """Run one of the harness's commands in the workspace, with its environment."""
        return subprocess.run(
            command,
            cwd=self.workspace,
            env=self.env(extra_env),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def prepare(self, commands: tuple[tuple[str, str], ...]) -> dict[str, str]:
        """Run the golden's `before` commands and return what each printed, by its name:
        the state the checks compare with. It is kept by the harness, not in the sandbox,
        so the agent can neither see nor change it."""
        state = {}
        for name, command in commands:
            proc = self.run([*SHELL, command], timeout=SETUP_TIMEOUT_S)
            if proc.returncode != 0:
                output = self.normalize((proc.stderr or proc.stdout).strip())[:500]
                raise SetupError(f"before command {name} exited with code {proc.returncode}: {output}")
            state[name] = proc.stdout.rstrip("\n")
        return state

    def run_checks(self, checks: tuple[GoldenCheck, ...], state: dict[str, str] | None = None) -> dict[str, CheckRun]:
        """Run each check in the workspace, with the `before` state as variables. A check
        that times out does not pass."""
        runs = {}
        for check in checks:
            try:
                proc = self.run([*SHELL, check.run], timeout=CHECK_TIMEOUT_S, extra_env=state)
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

    def stub_gaps(self) -> list[StubGap]:
        """The calls the fixture's stubs did not imitate, in order. A line that is not a
        gap (the agent may write to the file too) is skipped."""
        path = self.root / STUB_GAPS_FILE
        if not path.is_file():
            return []
        gaps = []
        for line in path.read_text(errors="replace").splitlines():
            try:
                entry = json.loads(line)
                gap = StubGap(str(entry["stub"]), tuple(str(a) for a in entry["argv"]), str(entry["reason"]))
            except ValueError, KeyError, TypeError:
                continue
            gaps.append(gap)
        return gaps

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


def _inside(path: Path, folder: Path) -> bool:
    try:
        return path.resolve().is_relative_to(folder.resolve())
    except OSError:
        return False
