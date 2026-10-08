"""Goldens: reference cases written only in the canonical vocabulary.

Each skill under evaluation has one golden, in a tree that mirrors the plugins, as
tests mirror the code in Java:

    plugins/commons/skills/git-workflow/SKILL.md                  the skill
    evals/plugins/commons/skills/git-workflow/golden.yaml         its cases
    evals/plugins/commons/skills/git-workflow/fixtures/<fixture>/ what each case starts from

The plugin and the skill come from the golden's path, not from its content.

YAML format:

    tools: [read, edit, search, execute]   # what the agent may use (default: all four)
    inspect:                               # shell commands whose output the judge sees,
      - git log --all --oneline            #   run before and after the agent
    before:                                # shell commands run before the agent, after the
      MAIN_BEFORE: git rev-parse main      #   fixture's setup: what each prints is a variable
                                           #   of the checks only, which the agent never sees
    rules_always_met:                      # checked by `rules_always_met` after the agent, in
      - name: "main was not rewritten"     #   every case: each passes if it exits with 0,
        run: git merge-base --is-ancestor "$MAIN_BEFORE" main  # all must
    rules_always_met_judged:               # judged by `rules_always_met_judged` on the final
      - "No secret was committed"          #   workspace, every case, all must be met
    cases:
      - id: case-identifier
        informative: true                  # optional: its quality metrics never fail the session
        fixture: some-fixture              # folder inside fixtures/
        prompts:                           # what the agent is asked, in one or more ways:
          - id: direct                     #   run i gets variant i mod n, in order
            text: "What the agent is asked to do"
          - id: casual
            text: "the same, asked otherwise"
        expected_calls:                    # must appear (in any order)
          - action: load_skill
            match: {name: "git-workflow"}
          - any_of:                        # one of them is enough
              - {action: read_file, match: {path: "@plugins/x/skills/y/SKILL.md"}}
              - {action: load_skill, match: {name: "y"}}
        forbidden_calls:                   # must not appear
          - action: shell
            match: {command: "*push*--force *"}
            not_match: {command: ["*--force-with-lease*"]}  # ...unless it matches one of these
        outcome_checks:                    # run by `outcome_checks`, all must pass
          - name: "the working tree is clean"
            run: test -z "$(git status --porcelain)"
        expected_outcome:                  # judged by `outcome`: one `optional` may be missed,
          - required: "The commit message is imperative"  # a `required` never
          - optional: "The scope names the module"       # (a plain text is `optional`)
    skill_loading:                         # whether the skill's description makes the agent
      fixture: some-fixture                #   load it when it should, and only then
      should_load:
        - "Save these changes in git"
      should_not_load:
        - "What does greet() do?"

The skill's absolute prohibitions go in `rules_always_met`, written as what must always
be true ("main was not rewritten"). What can be checked exactly goes in
`rules_always_met` and `outcome_checks`; `rules_always_met_judged` and `expected_outcome`
are only for what needs judgment. A check's name is its key between sessions: no two
checks of a case, the golden's `rules_always_met` included, share one; nor do two
criteria of a case's `expected_outcome`, whose key is their text.

Within a run, `outcome` fails if a `required` criterion is missed, or if more than one
`optional` is, or if every criterion is. The judge is not told which are required.

Each variant of `prompts` is the same request worded differently, as different users
would word it: a case passes or fails on all its runs together, and each variant's
rate is only reported. Run i gets variant i mod n, so with as many runs as variants
each runs once, and with fewer some never do. A variant's `id` is its key between
sessions: rewording its text or reordering the list keeps its history. The judge sees
the variant the run got.

A list inside `expected_calls` or `forbidden_calls` is spliced into it, so a YAML anchor
holding several matchers can be reused next to others (`- *no_force_push`). See
`matchers.py` for `match`, `not_match` and `by`.

A case with `informative: true` measures what the agent does not do well yet: its
non-strict metrics (`expected_calls`, `outcome_checks`, `outcome`) are scored and
reported but never fail the session, while its prohibitions stay strict. Once it passes
steadily, remove the mark and it guards against regressions like the rest.

Each prompt of `skill_loading` becomes a case of its own, `should-load-<slug>` or
`should-not-load-<slug>`, scored only by the `skill_loading` metric: no inspect, rules
or checks, and read-only tools, so a prompt that asks for work is a short run that
cannot do it. Rewording a prompt changes its id.

With `--baseline`, each case also runs without its skill. A call matcher that only the
skill can meet (loading it, reading its folder) is left out when comparing the two:
see `GoldenCase.comparable`.
"""

import re
import unicodedata
from dataclasses import dataclass, field, replace
from fnmatch import fnmatchcase
from pathlib import Path

import yaml

from marketplace_evals.matchers import AnyOf, CallMatcher, Expectation
from marketplace_evals.trace import ACTIONS, LOAD_SKILL, PLUGINS_PREFIX, READ_FILE

GOLDEN_FILE = "golden.yaml"
FIXTURES_DIR = "fixtures"

# What a golden may grant the agent, in Copilot's agent vocabulary: each runner
# translates it to its runtime's tools or permissions.
TOOLS = ("read", "edit", "search", "execute")

# Every key a golden and its cases may have: an unknown one (a typo, a renamed field) is
# an error, not a check or a rule that silently stops running. `anchors` holds YAML
# anchors and is ignored.
GOLDEN_KEYS = {
    "tools", "inspect", "before", "rules_always_met", "rules_always_met_judged", "anchors", "cases", "skill_loading",
}  # fmt: skip
CASE_KEYS = {
    "id", "fixture", "prompts", "informative", "expected_calls", "forbidden_calls", "outcome_checks", "expected_outcome",
}  # fmt: skip
SKILL_LOADING_KEYS = {"fixture", "should_load", "should_not_load"}

# What a `skill_loading` prompt runs with: enough to decide whether to load the skill,
# not to do what the prompt asks.
SKILL_LOADING_TOOLS = ("read", "search")
# The id of a `skill_loading` prompt's only variant: the prompt is already the case.
SKILL_LOADING_VARIANT = "prompt"
# Longest slug of a `skill_loading` prompt in its case's id.
MAX_SLUG = 50


class GoldenError(ValueError):
    """A golden that cannot be loaded: misplaced, or with unknown tools or actions, or
    malformed checks."""


@dataclass(frozen=True)
class GoldenCheck:
    """A shell command run in the workspace after the agent: passes if it exits with 0."""

    name: str  # its key between sessions: unique among a case's checks
    run: str


@dataclass(frozen=True)
class PromptVariant:
    """One way of asking what a case asks."""

    id: str  # its key between sessions: unique among the case's variants
    text: str


@dataclass(frozen=True)
class Criterion:
    """A criterion of `expected_outcome`, for the judge."""

    text: str  # what the judge is asked, and its key between sessions
    required: bool = False  # missing it fails the run, even if it is the only miss


@dataclass(frozen=True)
class GoldenCase:
    id: str
    plugin: str  # plugin under evaluation: the session loads it, and only it
    skill: str  # skill under evaluation
    fixture_dir: Path  # what the case starts from
    prompts: tuple[PromptVariant, ...]  # at least one; run i gets prompts[i % len(prompts)]
    expected_calls: list[Expectation]
    forbidden_calls: list[CallMatcher]
    tools: tuple[str, ...] = TOOLS
    inspect: tuple[str, ...] = ()  # commands whose output the judge sees
    # (name, command) run before the agent: what each prints is $name in the checks.
    before: tuple[tuple[str, str], ...] = ()
    rules_always_met: tuple[GoldenCheck, ...] = ()  # the skill's prohibitions, checked in every case
    rules_always_met_judged: list[str] = field(default_factory=list)  # the same, judged in every case
    outcome_checks: tuple[GoldenCheck, ...] = ()
    expected_outcome: list[Criterion] = field(default_factory=list)
    # A `skill_loading` prompt: whether the agent should load the skill. None in a case.
    should_load: bool | None = None
    # Measures what the agent does not do well yet: its non-strict metrics never fail.
    informative: bool = False

    @property
    def key(self) -> str:
        """Unique across goldens, and stable between sessions."""
        return f"{self.plugin}/{self.skill}/{self.id}"

    @property
    def has_variants(self) -> bool:
        """More than one prompt: the reports break the runs down by variant."""
        return len(self.prompts) > 1

    def variant(self, run: int) -> PromptVariant:
        """The prompt of the run with index `run` (from 0): the variants in order, again
        and again."""
        return self.prompts[run % len(self.prompts)]

    @property
    def skill_dir(self) -> str:
        """The skill's folder as the trace names it."""
        return f"{PLUGINS_PREFIX}{self.plugin}/skills/{self.skill}/"

    @property
    def is_skill_loading(self) -> bool:
        """A `skill_loading` prompt, not a case: it only checks whether the skill is loaded."""
        return self.should_load is not None

    def comparable(self) -> GoldenCase:
        """The case without the call matchers only its skill can meet, to compare runs
        with the skill and without it: a run without the skill cannot load it."""
        return replace(
            self,
            expected_calls=[e for e in self.expected_calls if not self.skill_only(e)],
            forbidden_calls=[m for m in self.forbidden_calls if not self.skill_only(m)],
        )

    def skill_only(self, expectation: Expectation) -> bool:
        """Whether only a run with the skill can meet `expectation`: it loads the skill by
        its name, or reads a file of the skill's folder. One alternative of an `any_of`
        is enough."""
        if isinstance(expectation, AnyOf):
            return any(self.skill_only(m) for m in expectation.matchers)
        if expectation.action == LOAD_SKILL:
            return "name" in expectation.match and fnmatchcase(self.skill, expectation.match["name"])
        if expectation.action == READ_FILE:
            return expectation.match.get("path", "").startswith(self.skill_dir)
        return False


def discover(goldens_dir: Path) -> list[Path]:
    """Every golden under `goldens_dir` (evals/plugins/), in its mirrored place."""
    return sorted(goldens_dir.glob(f"*/skills/*/{GOLDEN_FILE}"))


def load_goldens(path: Path) -> list[GoldenCase]:
    """The cases of a golden at `.../<plugin>/skills/<skill>/golden.yaml`."""
    plugin, kind, skill = path.parts[-4:-1]
    if kind != "skills":
        raise GoldenError(f"{path}: expected .../<plugin>/skills/<skill>/{GOLDEN_FILE}")
    data = yaml.safe_load(path.read_text())
    _known_keys(data, GOLDEN_KEYS, path, "the golden")
    for case in data["cases"]:
        _known_keys(case, CASE_KEYS, path, f"case {case.get('id')!r}")
    tools = _tools(data.get("tools", TOOLS), path)
    rules_always_met = _checks(data.get("rules_always_met", []), path)
    rules_always_met_judged = _rules(data.get("rules_always_met_judged", []), path)
    cases = [
        GoldenCase(
            id=case["id"],
            plugin=plugin,
            skill=skill,
            fixture_dir=path.parent / FIXTURES_DIR / case["fixture"],
            prompts=_prompts(case, path),
            expected_calls=[_expectation(m, path) for m in _spliced(case.get("expected_calls", []))],
            forbidden_calls=[_matcher(m, path) for m in _spliced(case.get("forbidden_calls", []))],
            tools=tools,
            inspect=tuple(data.get("inspect", [])),
            before=_before(data.get("before", {}), path),
            rules_always_met=rules_always_met,
            rules_always_met_judged=rules_always_met_judged,
            outcome_checks=_checks(case.get("outcome_checks", []), path),
            expected_outcome=_criteria(case.get("expected_outcome", []), path),
            informative=_informative(case, path),
        )
        for case in data["cases"]
    ]
    cases += _skill_loading(data.get("skill_loading"), plugin, skill, path)
    if repeated := sorted({c.id for c in cases if [d.id for d in cases].count(c.id) > 1}):
        raise GoldenError(f"{path}: case ids repeat: {repeated}")
    for case in cases:
        names = [c.name for c in (*case.rules_always_met, *case.outcome_checks)]
        if repeated := sorted({n for n in names if names.count(n) > 1}):
            raise GoldenError(f"{path}: check names repeat in case {case.id!r}: {repeated}")
        texts = [c.text for c in case.expected_outcome]
        if repeated := sorted({t for t in texts if texts.count(t) > 1}):
            raise GoldenError(f"{path}: expected_outcome criteria repeat in case {case.id!r}: {repeated}")
    return cases


def _skill_loading(raw: dict | None, plugin: str, skill: str, source: Path) -> list[GoldenCase]:
    """A case per prompt of the golden's `skill_loading`, with the skill to load or not."""
    if raw is None:
        return []
    _known_keys(raw, SKILL_LOADING_KEYS, source, "skill_loading")
    if "fixture" not in raw or not (raw.get("should_load") or raw.get("should_not_load")):
        raise GoldenError(f"{source}: skill_loading needs a `fixture` and `should_load` or `should_not_load` prompts")
    return [
        GoldenCase(
            id=f"{'should-load' if should_load else 'should-not-load'}-{_slug(prompt)}",
            plugin=plugin,
            skill=skill,
            fixture_dir=source.parent / FIXTURES_DIR / raw["fixture"],
            prompts=(PromptVariant(SKILL_LOADING_VARIANT, prompt),),
            expected_calls=[],
            forbidden_calls=[],
            tools=SKILL_LOADING_TOOLS,
            should_load=should_load,
        )
        for should_load, key in ((True, "should_load"), (False, "should_not_load"))
        for prompt in raw.get(key, [])
    ]


def _slug(text: str) -> str:
    """`Guarda estos cambios en git.` -> `guarda-estos-cambios-en-git`, cut at a word."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
    if len(slug) > MAX_SLUG:
        slug = slug[: MAX_SLUG + 1].rsplit("-", 1)[0]
    return slug


def _known_keys(data: dict, known: set[str], source: Path, where: str) -> None:
    if unknown := set(data) - known:
        raise GoldenError(f"{source}: unknown keys in {where}: {sorted(unknown)}; use {sorted(known)}")


def _informative(case: dict, source: Path) -> bool:
    value = case.get("informative", False)
    if not isinstance(value, bool):
        raise GoldenError(f"{source}: `informative` of case {case.get('id')!r} must be true or false, not {value!r}")
    return value


def _prompts(case: dict, source: Path) -> tuple[PromptVariant, ...]:
    """`prompts`: one or more `{id, text}`, with ids unique in the case."""
    where = f"case {case.get('id')!r}"
    raw = case.get("prompts")
    if not isinstance(raw, list) or not raw:
        raise GoldenError(f"{source}: {where} needs `prompts`, a list of at least one {{id, text}}")
    variants = []
    for item in raw:
        if (
            not isinstance(item, dict)
            or set(item) != {"id", "text"}
            or not all(isinstance(v, str) and v for v in item.values())
        ):
            raise GoldenError(f"{source}: a prompt of {where} needs an `id` and a `text`, and nothing else: {item!r}")
        variants.append(PromptVariant(item["id"], item["text"]))
    ids = [v.id for v in variants]
    if repeated := sorted({i for i in ids if ids.count(i) > 1}):
        raise GoldenError(f"{source}: prompt ids repeat in {where}: {repeated}")
    return tuple(variants)


def _tools(raw: list[str], source: Path) -> tuple[str, ...]:
    if unknown := set(raw) - set(TOOLS):
        raise GoldenError(f"{source}: unknown tools {sorted(unknown)}; use {list(TOOLS)}")
    return tuple(raw)


# A `before` name: a shell variable of the checks, in uppercase.
BEFORE_NAME = re.compile(r"[A-Z][A-Z0-9_]*")


def _before(raw: dict, source: Path) -> tuple[tuple[str, str], ...]:
    if not isinstance(raw, dict) or not all(isinstance(c, str) for c in raw.values()):
        raise GoldenError(f"{source}: `before` maps each name to a shell command: {raw!r}")
    if bad := sorted(name for name in raw if not BEFORE_NAME.fullmatch(str(name))):
        raise GoldenError(f"{source}: `before` names are uppercase shell variables (MAIN_BEFORE): {bad}")
    return tuple(raw.items())


def _checks(raw: list, source: Path) -> tuple[GoldenCheck, ...]:
    checks = []
    for item in raw:
        if (
            not isinstance(item, dict)
            or set(item) != {"name", "run"}
            or not all(isinstance(v, str) and v for v in item.values())
        ):
            raise GoldenError(f"{source}: a check needs a `name` and a `run`, and nothing else: {item!r}")
        checks.append(GoldenCheck(item["name"], item["run"]))
    return tuple(checks)


def _rules(raw: list, source: Path) -> list[str]:
    """`rules_always_met_judged`: texts, all of them required, so never `required:` or
    `optional:`."""
    if not all(isinstance(rule, str) and rule for rule in raw):
        raise GoldenError(f"{source}: each of rules_always_met_judged is a text, all of them required: {raw!r}")
    return list(raw)


def _criteria(raw: list, source: Path) -> list[Criterion]:
    """`expected_outcome`: each criterion a text (optional), or `required: <text>` or
    `optional: <text>`."""
    criteria = []
    for item in raw:
        if isinstance(item, str) and item:
            criteria.append(Criterion(item))
        elif (
            isinstance(item, dict)
            and len(item) == 1
            and set(item) <= {"required", "optional"}
            and isinstance(text := next(iter(item.values())), str)
            and text
        ):
            criteria.append(Criterion(text, required="required" in item))
        else:
            raise GoldenError(
                f"{source}: a criterion of expected_outcome is a text, `required: <text>` or `optional: <text>`: {item!r}"
            )
    return criteria


def _spliced(items: list) -> list[dict]:
    """The matchers, with the lists among them (reused anchors) spliced in."""
    return [m for item in items for m in (item if isinstance(item, list) else [item])]


def _expectation(raw: dict, source: Path) -> Expectation:
    if "any_of" in raw:
        return AnyOf([_matcher(m, source) for m in raw["any_of"]])
    return _matcher(raw, source)


def _matcher(raw: dict, source: Path) -> CallMatcher:
    if raw["action"] not in ACTIONS:
        raise GoldenError(f"{source}: unknown action {raw['action']!r}")
    not_match = {
        arg: [patterns] if isinstance(patterns, str) else list(patterns)
        for arg, patterns in raw.get("not_match", {}).items()
    }
    return CallMatcher(raw["action"], dict(raw.get("match", {})), raw.get("by"), not_match)
