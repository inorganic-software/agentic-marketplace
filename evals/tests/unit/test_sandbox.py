import os
import subprocess
from pathlib import Path

import pytest

from marketplace_evals import sandbox as sandbox_module
from marketplace_evals.goldens import GoldenCheck
from marketplace_evals.runtimes import AgentTask, Runner
from marketplace_evals.sandbox import Sandbox, SetupError
from marketplace_evals.trace import Trace


def fixture(tmp_path: Path, setup: str | None = None) -> tuple[Path, Path]:
    """A fixture with one file and an optional setup.sh, and a plugin with one skill."""
    fixture_dir, plugin_dir = tmp_path / "fixture", tmp_path / "commons"
    (fixture_dir / "src").mkdir(parents=True)
    (fixture_dir / "src" / "app.py").write_text("print('hi')\n")
    if setup is not None:
        (fixture_dir / "setup.sh").write_text(setup)
    (plugin_dir / "skills" / "s").mkdir(parents=True)
    (plugin_dir / "skills" / "s" / "SKILL.md").write_text("skill")
    return fixture_dir, plugin_dir


def sandbox(tmp_path: Path, setup: str | None = None) -> Sandbox:
    return Sandbox.create(tmp_path / "run", *fixture(tmp_path, setup))


def test_the_fixture_becomes_the_workspace_and_the_plugin_is_a_copy(tmp_path):
    fixture_dir, plugin_dir = fixture(tmp_path, setup="touch from-setup\n")
    box = Sandbox.create(tmp_path / "run", fixture_dir, plugin_dir)

    assert box.snapshot() == {"from-setup": "", "src/app.py": "print('hi')\n"}
    assert (box.plugin_dir / "skills" / "s" / "SKILL.md").read_text() == "skill"
    assert box.plugin_dir == box.plugins / "commons"
    assert not (fixture_dir / "from-setup").exists()  # the fixture is left untouched


def test_setup_sees_the_sandbox_and_isolated_git(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "users-own-gitconfig"))
    box = sandbox(tmp_path, setup=(
        'echo "$EVAL_SANDBOX" > sandbox.txt\n'
        'git config --global user.email > email.txt\n'
        'printf "#!/bin/sh\\necho stub\\n" > "$EVAL_SANDBOX/bin/gh" && chmod +x "$EVAL_SANDBOX/bin/gh"\n'
    ))  # fmt: skip

    assert (box.workspace / "sandbox.txt").read_text().strip() == str(box.root)
    assert (box.workspace / "email.txt").read_text().strip() == "eval-agent@example.com"
    gh = subprocess.run(["gh"], env=box.env(), capture_output=True, text=True)
    assert gh.stdout == "stub\n"  # the fixture's stub comes first in the PATH


def test_a_failing_setup_is_an_error(tmp_path):
    with pytest.raises(SetupError, match="exited with code 3"):
        sandbox(tmp_path, setup="exit 3\n")


def test_before_records_state_in_the_sandbox_and_a_failing_one_is_an_error(tmp_path):
    box = sandbox(tmp_path)
    box.prepare(('echo recorded > "$EVAL_SANDBOX/state"',))
    assert (box.root / "state").read_text() == "recorded\n"
    with pytest.raises(SetupError, match="'false; true' exited with code 1"):
        box.prepare(("false; true",))  # it fails at the first command that fails


def checks(box: Sandbox, **runs: str) -> dict[str, tuple[int, str]]:
    return {
        name: (r.exit_code, r.output)
        for name, r in box.run_checks(tuple(GoldenCheck(n, c) for n, c in runs.items())).items()
    }


def test_a_check_passes_if_it_exits_with_0_and_keeps_its_output_with_neutral_paths(tmp_path):
    box = sandbox(tmp_path)
    assert checks(box, ok="test -f src/app.py", ko='echo "$EVAL_SANDBOX/x"; exit 3') == {
        "ok": (0, ""),
        "ko": (3, "@sandbox/x"),
    }


def test_a_check_fails_at_any_line_or_any_command_of_a_pipeline(tmp_path):
    box = sandbox(tmp_path)
    result = checks(box, lines="false\ntrue", pipeline="false | true", substitution="x=$(false)\ntrue")
    assert {name: code != 0 for name, (code, _) in result.items()} == {
        "lines": True,
        "pipeline": True,
        "substitution": True,
    }


def test_a_check_that_times_out_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox_module, "CHECK_TIMEOUT_S", 0.5)
    code, output = checks(sandbox(tmp_path), slow="sleep 5")["slow"]
    assert code != 0
    assert "timed out" in output


def test_a_checks_output_is_cut_short(tmp_path):
    _, output = checks(sandbox(tmp_path), long="printf 'x%.0s' {1..2000}; exit 1")["long"]
    assert len(output) == sandbox_module.CHECK_OUTPUT_CHARS


def test_snapshot_leaves_out_git_but_inspect_shows_it_with_neutral_paths(tmp_path):
    box = sandbox(tmp_path, setup=(
        'git init -q --bare "$EVAL_SANDBOX/origin.git"\n'
        'git init -q && git add . && git commit -qm init && git remote add origin "$EVAL_SANDBOX/origin.git"\n'
    ))  # fmt: skip

    assert list(box.snapshot()) == ["src/app.py"]
    assert box.inspect(("git log --format=%s", "git remote -v | head -1", "true")) == (
        "$ git log --format=%s\ninit\n\n$ git remote -v | head -1\norigin\t@sandbox/origin.git (fetch)\n\n$ true"
    )


def test_normalize_makes_paths_relative_or_neutral(tmp_path):
    box = Sandbox(tmp_path, "commons")
    text = f"cat {tmp_path}/workspace/src/a.py {tmp_path}/plugins/commons/SKILL.md {tmp_path}/origin.git"
    assert box.normalize(text) == "cat src/a.py @plugins/commons/SKILL.md @sandbox/origin.git"


class FakeRunner(Runner):
    """Does in the workspace what a session would, with no runtime."""

    name = cli = "fake"

    def __init__(self, work: str):
        super().__init__("m")
        self.work = work

    def _launch(self, task: AgentTask, sandbox: Sandbox) -> Trace:
        subprocess.run(["bash", "-c", self.work], cwd=sandbox.workspace, env=sandbox.env(), check=True)
        return Trace(runtime="fake")


def task(tmp_path: Path, setup: str, inspect: tuple[str, ...]) -> AgentTask:
    fixture_dir, plugin_dir = fixture(tmp_path, setup)
    return AgentTask("p", plugin_dir, fixture_dir, ("read",), inspect)


def test_run_records_what_changed_in_the_files_and_in_the_inspected_state(tmp_path):
    t = task(tmp_path, "git init -q && git add . && git commit -qm init\n", ("git log --format=%s",))

    edit = FakeRunner("echo new > src/new.txt").run(t, tmp_path / "edit")
    assert edit.changed_files == ["src/new.txt"]
    assert edit.initial_state == edit.final_state

    commit = FakeRunner("git commit -q --allow-empty -m 'feat: x'").run(t, tmp_path / "commit")
    assert commit.changed_files == []
    assert commit.final_state == "$ git log --format=%s\nfeat: x\ninit"
    assert commit.changed


def test_run_records_state_before_the_agent_and_runs_the_checks_after_it(tmp_path):
    fixture_dir, plugin_dir = fixture(tmp_path, "git init -q && git add . && git commit -qm init\n")
    t = AgentTask(
        "p",
        plugin_dir,
        fixture_dir,
        ("read",),
        before=('git rev-parse HEAD > "$EVAL_SANDBOX/head-before"',),
        checks=(
            GoldenCheck("HEAD moved", 'test "$(git rev-parse HEAD)" != "$(cat "$EVAL_SANDBOX/head-before")"'),
            GoldenCheck("tree is clean", 'git status --porcelain\ntest -z "$(git status --porcelain)"'),
        ),
    )
    runner = FakeRunner("git commit -q --allow-empty -m x && touch untracked")
    runner.logs_dir = tmp_path / "logs"
    trace = runner.run(t, tmp_path / "case-0-abc")

    assert {name: r.passed for name, r in trace.check_runs.items()} == {"HEAD moved": True, "tree is clean": False}
    assert trace.check_runs["tree is clean"].output == "?? untracked"
    state = (tmp_path / "logs" / "case-0-abc.state.txt").read_text()
    assert "== checks\n[x] HEAD moved\n[ ] tree is clean\n    exit 1: ?? untracked\n" in state


def test_run_keeps_the_final_files_and_the_state_next_to_the_log(tmp_path):
    runner = FakeRunner("true")
    runner.logs_dir = tmp_path / "logs"
    runner.run(task(tmp_path, "git init -q\n", ("echo state",)), tmp_path / "case-0-abc")

    assert (tmp_path / "logs" / "case-0-abc" / "src" / "app.py").is_file()
    assert "== after\n$ echo state\nstate" in (tmp_path / "logs" / "case-0-abc.state.txt").read_text()


def test_the_users_git_configuration_does_not_reach_the_sandbox(tmp_path):
    env = Sandbox(tmp_path, "p").env({"PATH": os.environ["PATH"], "GIT_CONFIG_GLOBAL": "/home/me/.gitconfig"})
    assert env["GIT_CONFIG_GLOBAL"] == str(tmp_path / ".gitconfig")
    assert env["GIT_CONFIG_NOSYSTEM"] == "1"
    assert env["PATH"].startswith(f"{tmp_path / 'bin'}{os.pathsep}")


def test_a_baseline_sandbox_leaves_out_only_the_skill_under_evaluation(tmp_path):
    fixture_dir, plugin_dir = fixture(tmp_path)
    (plugin_dir / "skills" / "other").mkdir()
    (plugin_dir / "skills" / "other" / "SKILL.md").write_text("other")

    box = Sandbox.create(tmp_path / "run", fixture_dir, plugin_dir, without_skill="s")

    assert not (box.plugin_dir / "skills" / "s").exists()
    assert (box.plugin_dir / "skills" / "other" / "SKILL.md").is_file()
    assert (plugin_dir / "skills" / "s" / "SKILL.md").is_file()  # the plugin is left untouched


def test_a_baseline_of_a_skill_the_plugin_lacks_is_an_error(tmp_path):
    with pytest.raises(SetupError, match="no skill 'missing'"):
        Sandbox.create(tmp_path / "run", *fixture(tmp_path), without_skill="missing")
