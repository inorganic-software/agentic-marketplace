"""The `gh` stub of the git-workflow fixtures (fixtures/gh), run in a real sandbox
built from those fixtures."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from marketplace_evals.paths import GOLDENS_DIR, PLUGINS_DIR
from marketplace_evals.sandbox import Sandbox

FIXTURES = GOLDENS_DIR / "commons" / "skills" / "git-workflow" / "fixtures"

needs_jq = pytest.mark.skipif(shutil.which("jq") is None, reason="jq is not installed")


class Box:
    """A sandbox from a git-workflow fixture, where `gh` and `git` run as the agent's would."""

    def __init__(self, tmp_path: Path, fixture: str):
        self.sandbox = Sandbox.create(tmp_path / "run", FIXTURES / fixture, PLUGINS_DIR / "commons")

    def gh(self, *args: str) -> subprocess.CompletedProcess[str]:
        return self.sandbox.run(["gh", *args], timeout=30)

    def git(self, *args: str, origin: bool = False) -> str:
        command = ["git", *(["--git-dir", str(self.origin)] if origin else []), *args]
        return self.sandbox.run(command, timeout=30).stdout.strip()

    @property
    def origin(self) -> Path:
        return self.sandbox.root / "origin.git"

    def pr(self, number: int = 7) -> dict:
        return json.loads(self.git("log", "-1", "--format=%B", f"refs/pull/{number}/meta", origin=True))

    def gaps(self) -> list[dict]:
        path = self.sandbox.root / "stub-gaps.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def log(self) -> list[str]:
        return (self.sandbox.root / "gh.log").read_text().splitlines()


def test_help_shows_the_flags_and_does_nothing_else(tmp_path):
    box = Box(tmp_path, "approved-pr")
    main_before = box.git("rev-parse", "main", origin=True)

    for args in (["pr", "merge", "--help"], ["pr", "merge", "-h"], ["help", "pr", "merge"]):
        done = box.gh(*args)
        assert done.returncode == 0, done.stderr
        assert "--squash" in done.stdout and "--delete-branch" in done.stdout

    assert box.git("rev-parse", "main", origin=True) == main_before
    assert box.gaps() == []


def test_what_it_does_not_imitate_fails_as_gh_and_is_a_gap(tmp_path):
    box = Box(tmp_path, "approved-pr")

    flag = box.gh("pr", "merge", "--admin", "--squash")
    command = box.gh("pr", "lock")
    field = box.gh("pr", "view", "--json", "title,files")

    assert (flag.returncode, flag.stderr.strip()) == (1, "unknown flag: --admin")
    assert (command.returncode, command.stderr.strip()) == (1, 'unknown command "lock" for "gh pr"')
    assert field.returncode == 1 and field.stderr.startswith('Unknown JSON field: "files"')
    assert [g["reason"] for g in box.gaps()] == [
        "unknown flag: --admin",
        'unknown command "lock" for "gh pr"',
        'Unknown JSON field: "files"',
    ]
    assert box.gaps()[0] == {
        "stub": "gh",
        "argv": ["pr", "merge", "--admin", "--squash"],
        "reason": "unknown flag: --admin",
    }
    assert box.git("rev-parse", "--verify", "--quiet", "feat/add-farewell", origin=True)  # not merged


def test_a_modeled_error_is_not_a_gap(tmp_path):
    box = Box(tmp_path, "approved-pr")

    done = box.gh("pr", "merge")

    assert done.returncode == 1
    assert "--merge, --rebase, or --squash required" in done.stderr
    assert box.gaps() == []


def test_auth_token_fails_without_being_logged(tmp_path):
    box = Box(tmp_path, "approved-pr")

    assert box.gh("auth", "token").returncode == 1
    assert not (box.sandbox.root / "gh.log").exists()


def test_create_then_view_and_list_agree(tmp_path):
    box = Box(tmp_path, "feature-branch")
    assert box.sandbox.run(["git", "push", "-q", "-u", "origin", "feat/add-farewell"], timeout=30).returncode == 0

    done = box.gh("pr", "create", "--base", "main", "--title", "feat(greeting): add farewell", "--body", "Adds it.")

    assert done.stdout.strip() == "https://github.com/example/greeting/pull/7"
    assert box.pr() | {"createdAt": None} == box.pr() | {
        "number": 7,
        "title": "feat(greeting): add farewell",
        "body": "Adds it.",
        "state": "OPEN",
        "baseRefName": "main",
        "headRefName": "feat/add-farewell",
        "createdAt": None,
    }
    assert box.git("rev-parse", "refs/pull/7/head", origin=True) == box.git("rev-parse", "feat/add-farewell")
    assert box.git("log", "-1", "--format=%ce", "refs/pull/7/meta", origin=True) == "noreply@github.com"
    assert "title:\tfeat(greeting): add farewell" in box.gh("pr", "view").stdout
    assert box.gh("pr", "list").stdout.startswith("7\tfeat(greeting): add farewell\tfeat/add-farewell\tOPEN\t")
    again = box.gh("pr", "create", "--title", "t", "--body", "b")
    assert again.returncode == 1 and "already exists" in again.stderr


def test_create_needs_the_branch_pushed_and_a_title_and_body(tmp_path):
    box = Box(tmp_path, "feature-branch")

    unpushed = box.gh("pr", "create", "--title", "t", "--body", "b")
    box.sandbox.run(["git", "push", "-q", "-u", "origin", "feat/add-farewell"], timeout=30)
    untitled = box.gh("pr", "create")
    filled = box.gh("pr", "create", "--fill")

    assert unpushed.returncode == 1 and "must first push the current branch" in unpushed.stderr
    assert untitled.returncode == 1 and "must provide `--title` and `--body`" in untitled.stderr
    assert filled.returncode == 0
    assert box.pr()["title"] == "feat: add farewell"  # the only commit's subject


def test_squash_merge_is_committed_by_github_and_deletes_the_branch(tmp_path):
    box = Box(tmp_path, "approved-pr")
    main_before = box.git("rev-parse", "main", origin=True)

    done = box.gh("pr", "merge", "--squash", "--delete-branch")

    assert done.returncode == 0, done.stderr
    assert "✓ Squashed and merged pull request example/greeting#7" in done.stdout
    assert box.git("rev-list", "--count", f"{main_before}..main", origin=True) == "1"
    assert box.git("log", "-1", "--format=%s|%ce|%P", "main", origin=True) == (
        f"feat(greeting): add farewell (#7)|noreply@github.com|{main_before}"
    )
    assert box.pr()["state"] == "MERGED"
    assert box.git("branch", "--list", "feat/add-farewell", origin=True) == ""
    assert box.git("branch", "--show-current") == "main"
    assert box.git("branch", "--list", "feat/add-farewell") == ""
    # It does not pull: the local main stays where it was.
    assert box.git("rev-parse", "main") == main_before


def test_merge_respects_main_protection(tmp_path):
    box = Box(tmp_path, "approved-pr")
    pr_file = box.sandbox.root / "gh" / "pr.json"
    pr_file.write_text(json.dumps(json.loads(pr_file.read_text()) | {"reviewDecision": "REVIEW_REQUIRED"}))
    main_before = box.git("rev-parse", "main", origin=True)

    blocked = box.gh("pr", "merge", "--squash")
    auto = box.gh("pr", "merge", "--squash", "--auto")

    assert blocked.returncode == 1 and "the base branch policy prohibits the merge" in blocked.stderr
    assert auto.returncode == 0 and "will be automatically merged via squash" in auto.stdout
    assert box.pr()["autoMergeRequest"] == {"mergeMethod": "SQUASH"}
    assert box.git("rev-parse", "main", origin=True) == main_before


def test_failing_checks_block_the_merge_and_fail_pr_checks(tmp_path):
    box = Box(tmp_path, "approved-pr")
    pr_file = box.sandbox.root / "gh" / "pr.json"
    pr = json.loads(pr_file.read_text())
    pr_file.write_text(json.dumps(pr | {"checks": [{"name": "tests", "conclusion": "FAILURE"}]}))

    checks = box.gh("pr", "checks")

    assert checks.returncode == 1 and checks.stdout.startswith("tests\tfail\t")
    assert box.gh("pr", "merge", "--squash").returncode == 1


@needs_jq
def test_json_and_jq(tmp_path):
    box = Box(tmp_path, "approved-pr")

    view = box.gh("pr", "view", "--json", "number,reviewDecision,mergeStateStatus")
    jq = box.gh("pr", "view", "--json", "title", "--jq", ".title")
    status = box.gh("pr", "status", "--json", "number", "--jq", ".currentBranch.number")

    assert json.loads(view.stdout) == {"number": 7, "reviewDecision": "APPROVED", "mergeStateStatus": "CLEAN"}
    assert jq.stdout == "feat(greeting): add farewell\n"
    assert status.stdout == "7\n"


def test_every_call_is_logged_for_the_judge(tmp_path):
    box = Box(tmp_path, "approved-pr")

    box.gh("pr", "view")
    box.gh("pr", "create", "--title", "feat: x y", "--body", "")

    assert box.log() == ["gh pr view", "gh pr create --title 'feat: x y' --body ''"]


def test_it_works_in_the_agents_environment_without_eval_sandbox(tmp_path):
    box = Box(tmp_path, "approved-pr")
    sandbox = box.sandbox

    done = subprocess.run(
        ["gh", "pr", "merge", "--squash"],
        cwd=sandbox.workspace,
        env=sandbox.agent_env(),
        capture_output=True,
        text=True,
    )

    assert done.returncode == 0, done.stderr
    assert box.pr()["state"] == "MERGED"
