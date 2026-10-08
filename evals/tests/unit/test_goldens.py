from pathlib import Path

import pytest

from marketplace_evals.goldens import (
    GOLDEN_FILE,
    Criterion,
    GoldenCheck,
    GoldenError,
    PromptVariant,
    discover,
    load_goldens,
)
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
    path = golden(tmp_path, "cases:\n  - {id: c, fixture: f, prompts: [{id: v, text: p}]}\n")
    [case] = load_goldens(path)
    assert (case.plugin, case.skill, case.key) == ("p", "s", "p/s/c")
    assert case.fixture_dir == path.parent / "fixtures" / "f"
    assert case.tools == ("read", "edit", "search", "execute")


def test_tools_inspect_and_judged_rules_are_shared_by_every_case(tmp_path):
    path = golden(
        tmp_path,
        (
            "tools: [read, execute]\ninspect: [git log]\nrules_always_met_judged: [r]\n"
            "cases:\n  - {id: a, fixture: f, prompts: [{id: v, text: p}]}\n  - {id: b, fixture: f, prompts: [{id: v, text: p}]}\n"
        ),
    )
    assert {(c.tools, tuple(c.inspect), tuple(c.rules_always_met_judged)) for c in load_goldens(path)} == {
        (("read", "execute"), ("git log",), ("r",))
    }


def test_before_and_rules_always_met_are_shared_and_outcome_checks_belong_to_their_case(tmp_path):
    path = golden(
        tmp_path,
        (
            'before: [git rev-parse main > "$EVAL_SANDBOX/main"]\n'
            "rules_always_met:\n  - {name: r, run: 'true'}\n"
            "cases:\n"
            "  - {id: a, fixture: f, prompts: [{id: v, text: p}], outcome_checks: [{name: clean, run: 'test -z x'}]}\n"
            "  - {id: b, fixture: f, prompts: [{id: v, text: p}]}\n"
        ),
    )
    a, b = load_goldens(path)
    assert a.before == b.before == ('git rev-parse main > "$EVAL_SANDBOX/main"',)
    assert a.rules_always_met == b.rules_always_met == (GoldenCheck("r", "true"),)
    assert a.outcome_checks == (GoldenCheck("clean", "test -z x"),)
    assert b.outcome_checks == ()


@pytest.mark.parametrize(
    "check",
    ["{name: n}", "{run: 'true'}", "{name: n, run: 'true', when: x}", "{name: '', run: 'true'}", "'true'"],
)
def test_a_check_needs_a_name_and_a_run(tmp_path, check):
    with pytest.raises(GoldenError, match="needs a `name` and a `run`"):
        load_goldens(
            golden(
                tmp_path,
                f"cases:\n  - {{id: c, fixture: f, prompts: [{{id: v, text: p}}], outcome_checks: [{check}]}}\n",
            )
        )


def test_check_names_are_unique_in_a_case(tmp_path):
    """The name is the check's key between sessions: two with one name would mix up."""
    with pytest.raises(GoldenError, match=r"repeat in case 'c'.*'n'"):
        load_goldens(
            golden(
                tmp_path,
                (
                    "rules_always_met: [{name: n, run: 'true'}]\n"
                    "cases:\n  - {id: c, fixture: f, prompts: [{id: v, text: p}], outcome_checks: [{name: n, run: 'false'}]}\n"
                ),
            )
        )


def test_an_outcome_criterion_is_required_or_optional_and_a_plain_text_is_optional(tmp_path):
    path = golden(
        tmp_path,
        "cases:\n  - id: c\n    fixture: f\n    prompts: [{id: v, text: p}]\n    expected_outcome:\n"
        "      - required: a\n      - optional: b\n      - c\n",
    )
    [case] = load_goldens(path)
    assert case.expected_outcome == [Criterion("a", required=True), Criterion("b"), Criterion("c")]


@pytest.mark.parametrize(
    "criterion",
    ["{required: a, optional: b}", "{mandatory: a}", "{required: ''}", "{required: [a]}", "''", "[a]"],
)
def test_a_malformed_outcome_criterion_is_an_error(tmp_path, criterion):
    with pytest.raises(GoldenError, match="a criterion of expected_outcome is a text"):
        load_goldens(
            golden(
                tmp_path,
                f"cases:\n  - {{id: c, fixture: f, prompts: [{{id: v, text: p}}], expected_outcome: [{criterion}]}}\n",
            )
        )


def test_outcome_criteria_are_unique_in_a_case_however_marked(tmp_path):
    """Their text is their key between sessions."""
    with pytest.raises(GoldenError, match=r"expected_outcome criteria repeat in case 'c': \['a'\]"):
        load_goldens(
            golden(
                tmp_path,
                "cases:\n  - {id: c, fixture: f, prompts: [{id: v, text: p}], expected_outcome: [{required: a}, a]}\n",
            )
        )


def test_judged_rules_are_plain_texts_all_required(tmp_path):
    with pytest.raises(GoldenError, match="each of rules_always_met_judged is a text"):
        load_goldens(golden(tmp_path, "rules_always_met_judged: [{required: r}]\ncases: []\n"))


def test_a_reused_list_of_matchers_is_spliced_into_the_others(tmp_path):
    path = golden(
        tmp_path,
        (
            "anchors:\n  two: &two\n    - {action: shell}\n    - {action: write_file}\n"
            "cases:\n  - {id: c, fixture: f, prompts: [{id: v, text: p}], forbidden_calls: [*two, {action: edit_file}]}\n"
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
                ("cases:\n  - {id: c, fixture: f, prompts: [{id: v, text: p}], expected_calls: [{action: browse}]}\n"),
                rel="plugins/p/skills/other",
            )
        )


def test_unknown_keys_are_errors_so_nothing_stops_running_silently(tmp_path):
    with pytest.raises(GoldenError, match=r"unknown keys in the golden: \['rules'\]"):
        load_goldens(golden(tmp_path, "rules: [r]\ncases: []\n"))
    with pytest.raises(GoldenError, match=r"unknown keys in case 'c': \['outcome_check'\]"):
        load_goldens(
            golden(
                tmp_path,
                "cases:\n  - {id: c, fixture: f, prompts: [{id: v, text: p}], outcome_check: []}\n",
                rel="plugins/p/skills/o",
            )
        )


def test_a_case_is_required_unless_marked_informative(tmp_path):
    path = golden(
        tmp_path,
        "cases:\n  - {id: a, fixture: f, prompts: [{id: v, text: p}]}\n  - {id: b, fixture: f, prompts: [{id: v, text: p}], informative: true}\n",
    )
    assert [c.informative for c in load_goldens(path)] == [False, True]


def test_informative_must_be_a_boolean(tmp_path):
    """`informative: "no"` would otherwise read as true."""
    with pytest.raises(GoldenError, match="must be true or false"):
        load_goldens(
            golden(tmp_path, "cases:\n  - {id: c, fixture: f, prompts: [{id: v, text: p}], informative: 'no'}\n")
        )


def test_a_golden_outside_a_skill_folder_is_an_error(tmp_path):
    with pytest.raises(GoldenError, match="skills"):
        load_goldens(golden(tmp_path, "cases: []\n", rel="plugins/p/agents/a"))


def test_comparable_leaves_out_the_calls_only_the_skill_can_make(tmp_path):
    [case] = load_goldens(
        golden(
            tmp_path,
            "cases:\n"
            "  - id: c\n"
            "    fixture: f\n"
            "    prompts: [{id: v, text: p}]\n"
            "    expected_calls:\n"
            "      - {action: load_skill, match: {name: s}}\n"
            "      - any_of:\n"
            "          - {action: load_skill, match: {name: other}}\n"
            "          - {action: read_file, match: {path: '@plugins/p/skills/s/SKILL.md'}}\n"
            "      - {action: load_skill, match: {name: other}}\n"
            "      - {action: load_skill}\n"
            "      - {action: read_file, match: {path: '@plugins/p/skills/other/SKILL.md'}}\n"
            "      - {action: shell, match: {command: '*git fetch*'}}\n"
            "    forbidden_calls:\n"
            "      - {action: load_skill, match: {name: 's*'}}\n"
            "      - {action: shell, match: {command: '*push*'}}\n",
        )
    )
    comparable = case.comparable()

    # Another skill, any skill, or the plugin's other files can be loaded without it.
    assert [str(e) for e in comparable.expected_calls] == [
        "*: load_skill(name~'other')",
        "*: load_skill()",
        "*: read_file(path~'@plugins/p/skills/other/SKILL.md')",
        "*: shell(command~'*git fetch*')",
    ]
    assert [str(m) for m in comparable.forbidden_calls] == ["*: shell(command~'*push*')"]
    assert len(case.expected_calls) == 6  # the case itself is left as it was


def test_a_case_without_calls_only_the_skill_can_make_is_its_own_comparable(tmp_path):
    [case] = load_goldens(
        golden(tmp_path, "cases:\n  - {id: c, fixture: f, prompts: [{id: v, text: p}], expected_outcome: [x]}\n")
    )
    assert case.comparable() == case


SKILL_LOADING = (
    "cases: []\n"
    "skill_loading:\n"
    "  fixture: f\n"
    "  should_load: ['Guarda estos cambios en git.']\n"
    "  should_not_load: ['¿Qué hace la función greet?']\n"
)


def test_each_skill_loading_prompt_is_a_read_only_case_named_after_it(tmp_path):
    path = golden(tmp_path, "inspect: [git log]\nrules_always_met: [{name: n, run: 'true'}]\n" + SKILL_LOADING)
    load, dont = load_goldens(path)

    assert (load.id, load.prompts[0].text, load.should_load) == (
        "should-load-guarda-estos-cambios-en-git",
        "Guarda estos cambios en git.",
        True,
    )
    assert (dont.id, dont.should_load) == ("should-not-load-que-hace-la-funcion-greet", False)
    for case in (load, dont):
        assert case.is_skill_loading and case.fixture_dir == path.parent / "fixtures" / "f"
        assert case.tools == ("read", "search")
        # Only whether the skill is loaded: nothing else of the golden reaches them.
        assert (case.inspect, case.rules_always_met, case.expected_calls, case.forbidden_calls) == ((), (), [], [])


def test_a_case_is_not_a_skill_loading_prompt(tmp_path):
    [case] = load_goldens(golden(tmp_path, "cases:\n  - {id: c, fixture: f, prompts: [{id: v, text: p}]}\n"))
    assert case.should_load is None and not case.is_skill_loading


def test_a_long_skill_loading_prompt_is_cut_at_a_word(tmp_path):
    prompt = "Mi rama se ha quedado atrás respecto a main, ¿cómo la pongo al día?"
    path = golden(tmp_path, f"cases: []\nskill_loading: {{fixture: f, should_load: ['{prompt}']}}\n")
    [case] = load_goldens(path)
    assert case.id == "should-load-mi-rama-se-ha-quedado-atras-respecto-a-main-como"


@pytest.mark.parametrize(
    ("section", "error"),
    [
        ("{fixture: f, should_load: [a], extra: 1}", r"unknown keys in skill_loading: \['extra'\]"),
        ("{should_load: [a]}", "needs a `fixture`"),
        ("{fixture: f}", "`should_load` or `should_not_load`"),
        ("{fixture: f, should_load: ['A.', 'a']}", r"case ids repeat: \['should-load-a'\]"),
    ],
)
def test_a_malformed_skill_loading_is_an_error(tmp_path, section, error):
    with pytest.raises(GoldenError, match=error):
        load_goldens(golden(tmp_path, f"cases: []\nskill_loading: {section}\n"))


def test_a_case_asks_its_prompt_in_variants_that_runs_get_in_order(tmp_path):
    [case] = load_goldens(
        golden(tmp_path, "cases:\n  - {id: c, fixture: f, prompts: [{id: a, text: A}, {id: b, text: B}]}\n")
    )
    assert case.prompts == (PromptVariant("a", "A"), PromptVariant("b", "B"))
    assert case.has_variants
    assert [case.variant(i).id for i in range(5)] == ["a", "b", "a", "b", "a"]


def test_a_single_variant_is_not_broken_down(tmp_path):
    [case] = load_goldens(golden(tmp_path, "cases:\n  - {id: c, fixture: f, prompts: [{id: a, text: A}]}\n"))
    assert not case.has_variants and case.variant(2).id == "a"


@pytest.mark.parametrize(
    ("prompts", "error"),
    [
        ("prompt: p", "unknown keys"),  # the old single prompt
        ("prompts: []", "needs `prompts`"),
        ("prompts: p", "needs `prompts`"),
        ("prompts: [p]", "needs an `id` and a `text`"),
        ("prompts: [{id: a}]", "needs an `id` and a `text`"),
        ("prompts: [{id: a, text: ''}]", "needs an `id` and a `text`"),
        ("prompts: [{id: a, text: A, lang: es}]", "needs an `id` and a `text`"),
        ("prompts: [{id: a, text: A}, {id: a, text: B}]", "prompt ids repeat"),
    ],
)
def test_malformed_prompts_are_errors(tmp_path, prompts, error):
    with pytest.raises(GoldenError, match=error):
        load_goldens(golden(tmp_path, f"cases:\n  - {{id: c, fixture: f, {prompts}}}\n"))
