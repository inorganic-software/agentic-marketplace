from pathlib import Path

import pytest

from marketplace_evals.goldens import GOLDEN_FILE, GoldenError, discover, load_goldens
from marketplace_evals.matchers import AnyOf
from marketplace_evals.paths import EVALS_DIR, GOLDENS_DIR, PLUGINS_DIR
from marketplace_evals.trace import PLUGINS_PREFIX

GOLDENS = discover(GOLDENS_DIR)


def test_every_golden_mirrors_a_skill_of_a_plugin():
    """A golden out of place would never run: discover() only finds the mirrored ones."""
    assert sorted(GOLDENS_DIR.rglob(GOLDEN_FILE)) == GOLDENS


@pytest.mark.parametrize("path", GOLDENS, ids=[str(p.parent.relative_to(EVALS_DIR)) for p in GOLDENS])
def test_golden_points_at_things_that_exist(path):
    """A typo in a golden would fail every run for a reason that is not the agent."""
    cases = load_goldens(path)
    assert cases
    assert len({c.id for c in cases}) == len(cases), "case ids repeat"
    for case in cases:
        plugin = PLUGINS_DIR / case.plugin
        assert (plugin / ".claude-plugin" / "plugin.json").is_file(), case.plugin
        assert (plugin / "skills" / case.skill / "SKILL.md").is_file(), case.skill
        assert case.fixture_dir.is_dir(), case.fixture_dir
        matchers = [m for e in case.expected_calls for m in (e.matchers if isinstance(e, AnyOf) else [e])]
        for m in matchers:
            if m.action == "read_file" and m.match.get("path", "").startswith(PLUGINS_PREFIX):
                assert (PLUGINS_DIR / m.match["path"].removeprefix(PLUGINS_PREFIX)).is_file(), m.match["path"]
            if m.action == "load_skill":
                assert (plugin / "skills" / m.match["name"] / "SKILL.md").is_file(), m.match["name"]


@pytest.mark.parametrize("path", GOLDENS, ids=[str(p.parent.relative_to(EVALS_DIR)) for p in GOLDENS])
def test_every_fixture_is_used(path):
    fixtures = path.parent / "fixtures"
    used = {case.fixture_dir.name for case in load_goldens(path)}
    present = {d.name for d in fixtures.iterdir() if d.is_dir()} if fixtures.is_dir() else set()
    assert present <= used, f"unused fixtures: {sorted(present - used)}"


def golden(tmp_path: Path, text: str, rel: str = "plugins/p/skills/s") -> Path:
    path = tmp_path / rel / GOLDEN_FILE
    path.parent.mkdir(parents=True)
    path.write_text(text)
    return path


def test_plugin_and_skill_come_from_the_path(tmp_path):
    path = golden(tmp_path, "cases:\n  - {id: c, fixture: f, prompt: p}\n")
    [case] = load_goldens(path)
    assert (case.plugin, case.skill, case.key) == ("p", "s", "p/s/c")
    assert case.fixture_dir == path.parent / "fixtures" / "f"
    assert case.tools == ("read", "edit", "search", "execute")


def test_tools_inspect_and_rules_are_shared_by_every_case(tmp_path):
    path = golden(
        tmp_path,
        (
            "tools: [read, execute]\ninspect: [git log]\nrules: [r]\n"
            "cases:\n  - {id: a, fixture: f, prompt: p}\n  - {id: b, fixture: f, prompt: p}\n"
        ),
    )
    assert {(c.tools, tuple(c.inspect), tuple(c.rules)) for c in load_goldens(path)} == {
        (("read", "execute"), ("git log",), ("r",))
    }


def test_a_reused_list_of_matchers_is_spliced_into_the_others(tmp_path):
    path = golden(
        tmp_path,
        (
            "anchors:\n  two: &two\n    - {action: shell}\n    - {action: write_file}\n"
            "cases:\n  - {id: c, fixture: f, prompt: p, forbidden_calls: [*two, {action: edit_file}]}\n"
        ),
    )
    [case] = load_goldens(path)
    assert [m.action for m in case.forbidden_calls] == ["shell", "write_file", "edit_file"]


def test_unknown_tools_and_actions_are_errors(tmp_path):
    with pytest.raises(GoldenError, match="unknown tools"):
        load_goldens(golden(tmp_path, "tools: [web]\ncases: []\n"))
    with pytest.raises(GoldenError, match="unknown action"):
        load_goldens(
            golden(
                tmp_path,
                ("cases:\n  - {id: c, fixture: f, prompt: p, expected_calls: [{action: browse}]}\n"),
                rel="plugins/p/skills/other",
            )
        )


def test_a_golden_outside_a_skill_folder_is_an_error(tmp_path):
    with pytest.raises(GoldenError, match="skills"):
        load_goldens(golden(tmp_path, "cases: []\n", rel="plugins/p/agents/a"))
