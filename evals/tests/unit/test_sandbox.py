import json
import os
import subprocess
from pathlib import Path

import pytest

from marketplace_evals import sandbox as sandbox_module
from marketplace_evals.goldens import GoldenCheck
from marketplace_evals.runtimes import AgentTask, Runner
from marketplace_evals.runtimes.claude_code.runner import write_login
from marketplace_evals.sandbox import Sandbox, SetupError
from marketplace_evals.trace import StubGap, Trace


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


def test_before_returns_what_each_command_printed_and_a_failing_one_is_an_error(tmp_path):
    box = sandbox(tmp_path)
    assert box.prepare((("STATE", "echo recorded"), ("TWO", "printf 'a\\nb\\n'"))) == {
        "STATE": "recorded",
        "TWO": "a\nb",
    }
    assert sorted(p.name for p in box.root.iterdir()) == [".gitconfig", "bin", "home", "plugins", "tmp", "workspace"]
    with pytest.raises(SetupError, match="before command BROKEN exited with code 1"):
        box.prepare((("BROKEN", "false; true"),))  # it fails at the first command that fails


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
        before=(("HEAD_BEFORE", "git rev-parse HEAD"),),
        checks=(
            GoldenCheck("HEAD moved", 'test "$(git rev-parse HEAD)" != "$HEAD_BEFORE"'),
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


def test_the_users_git_configuration_does_not_reach_the_sandbox(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/home/me/.gitconfig")
    for env in (Sandbox(tmp_path, "p").env(), Sandbox(tmp_path, "p").agent_env()):
        assert env["GIT_CONFIG_GLOBAL"] == str(tmp_path / ".gitconfig")
        assert env["GIT_CONFIG_NOSYSTEM"] == "1"
        assert env["PATH"].startswith(f"{tmp_path / 'bin'}{os.pathsep}")


def test_the_agent_gets_a_clean_environment(tmp_path, monkeypatch):
    venv_bin = sandbox_module.REPO_DIR / "evals" / ".venv" / "bin"
    monkeypatch.setenv("PATH", os.pathsep.join([str(venv_bin), "/usr/bin", "/bin"]))
    for name in (
        "CLAUDE_CODE_SESSION_ID",
        "VIRTUAL_ENV",
        "EVALS_VERTEX_PROJECT",
        "ACTIONS_ID_TOKEN_REQUEST_TOKEN",
        "PWD",
    ):
        monkeypatch.setenv(name, "leak")
    monkeypatch.setenv("LANG", "es_ES.UTF-8")
    monkeypatch.setenv("LC_ALL", "es_ES.UTF-8")
    box = Sandbox(tmp_path, "p")

    env = box.agent_env({"ANTHROPIC_API_KEY": "key"})

    assert "leak" not in env.values()
    assert "EVAL_SANDBOX" not in env
    assert env["PATH"] == os.pathsep.join([str(box.bin), "/usr/bin", "/bin"])  # without the repo's virtualenv
    assert (env["HOME"], env["TMPDIR"]) == (str(box.home), str(box.tmp))
    assert (env["LANG"], env["LC_ALL"], env["ANTHROPIC_API_KEY"]) == ("es_ES.UTF-8", "es_ES.UTF-8", "key")
    assert box.env()["EVAL_SANDBOX"] == str(tmp_path)  # the harness's own commands keep it


def test_the_sandbox_root_is_named_apart_from_the_run(tmp_path):
    fixture_dir, plugin_dir = fixture(tmp_path)
    box = Sandbox.create(tmp_path / "tmpx1", fixture_dir, plugin_dir, name="case-v-0-x1")
    assert (box.root.name, box.log_name) == ("tmpx1", "case-v-0-x1")
    assert box.home.is_dir() and box.tmp.is_dir()


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


def test_stub_gaps_are_read_from_the_sandbox_skipping_what_is_not_a_gap(tmp_path):
    box = sandbox(tmp_path)
    assert box.stub_gaps() == []
    (box.root / sandbox_module.STUB_GAPS_FILE).write_text(
        '{"stub": "gh", "argv": ["pr", "create", "--fill"], "reason": "unknown flag: --fill"}\n'
        "not json\n"
        '{"stub": "gh"}\n'
        "[1, 2]\n"
        '{"stub": "gh", "argv": ["foo"], "reason": "unknown command"}\n'
    )
    gaps = box.stub_gaps()
    assert gaps == [
        StubGap("gh", ("pr", "create", "--fill"), "unknown flag: --fill"),
        StubGap("gh", ("foo",), "unknown command"),
    ]
    assert str(gaps[0]) == "gh pr create --fill: unknown flag: --fill"


def test_run_records_the_stub_gaps_of_the_agent_only(tmp_path):
    gap = '{"stub": "gh", "argv": ["x"], "reason": "r"}'
    fixture_dir, plugin_dir = fixture(tmp_path)
    t = AgentTask(
        "p",
        plugin_dir,
        fixture_dir,
        ("read",),
        checks=(GoldenCheck("a check's own gap", f"echo '{gap}' >> \"$EVAL_SANDBOX/stub-gaps.jsonl\""),),
    )
    trace = FakeRunner(f"echo '{gap}' >> \"$EVAL_SANDBOX/stub-gaps.jsonl\"").run(t, tmp_path / "run")
    assert trace.stub_gaps == [StubGap("gh", ("x",), "r")]


def test_claude_code_gets_only_what_says_which_login_to_use(tmp_path, monkeypatch):
    user_home = tmp_path / "user"
    user_home.mkdir()
    (user_home / ".claude.json").write_text(
        json.dumps(
            {"oauthAccount": {"emailAddress": "a@b.c"}, "userID": "u", "projects": {"/repo": {}}, "mcpServers": {}}
        )
    )
    monkeypatch.setattr(Path, "home", lambda: user_home)
    write_login(tmp_path)

    assert json.loads((tmp_path / ".claude.json").read_text()) == {
        "oauthAccount": {"emailAddress": "a@b.c"},
        "userID": "u",
    }
