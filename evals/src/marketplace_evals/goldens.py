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
    rules:                                 # judged by `rules` on the final workspace,
      - "No commit lands on main"          #   every case, all must be met
    cases:
      - id: case-identifier
        fixture: some-fixture              # folder inside fixtures/
        prompt: "What the agent is asked to do"
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
        expected_outcome:                  # judged by `outcome`
          - "The commit message follows Conventional Commits"

A list inside `expected_calls` or `forbidden_calls` is spliced into it, so a YAML anchor
holding several matchers can be reused next to others (`- *no_force_push`). See
`matchers.py` for `match`, `not_match` and `by`.
"""

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from marketplace_evals.matchers import AnyOf, CallMatcher, Expectation
from marketplace_evals.trace import ACTIONS

GOLDEN_FILE = "golden.yaml"
FIXTURES_DIR = "fixtures"

# What a golden may grant the agent, in Copilot's agent vocabulary: each runner
# translates it to its runtime's tools or permissions.
TOOLS = ("read", "edit", "search", "execute")


class GoldenError(ValueError):
    """A golden that cannot be loaded: misplaced, or with unknown tools or actions."""


@dataclass(frozen=True)
class GoldenCase:
    id: str
    plugin: str  # plugin under evaluation: the session loads it, and only it
    skill: str  # skill under evaluation
    fixture_dir: Path  # what the case starts from
    prompt: str
    expected_calls: list[Expectation]
    forbidden_calls: list[CallMatcher]
    tools: tuple[str, ...] = TOOLS
    inspect: tuple[str, ...] = ()  # commands whose output the judge sees
    rules: list[str] = field(default_factory=list)  # criteria shared by every case
    expected_outcome: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        """Unique across goldens, and stable between sessions."""
        return f"{self.plugin}/{self.skill}/{self.id}"


def discover(goldens_dir: Path) -> list[Path]:
    """Every golden under `goldens_dir` (evals/plugins/), in its mirrored place."""
    return sorted(goldens_dir.glob(f"*/skills/*/{GOLDEN_FILE}"))


def load_goldens(path: Path) -> list[GoldenCase]:
    """The cases of a golden at `.../<plugin>/skills/<skill>/golden.yaml`."""
    plugin, kind, skill = path.parts[-4:-1]
    if kind != "skills":
        raise GoldenError(f"{path}: expected .../<plugin>/skills/<skill>/{GOLDEN_FILE}")
    data = yaml.safe_load(path.read_text())
    tools = _tools(data.get("tools", TOOLS), path)
    return [
        GoldenCase(
            id=case["id"],
            plugin=plugin,
            skill=skill,
            fixture_dir=path.parent / FIXTURES_DIR / case["fixture"],
            prompt=case["prompt"],
            expected_calls=[_expectation(m, path) for m in _spliced(case.get("expected_calls", []))],
            forbidden_calls=[_matcher(m, path) for m in _spliced(case.get("forbidden_calls", []))],
            tools=tools,
            inspect=tuple(data.get("inspect", [])),
            rules=list(data.get("rules", [])),
            expected_outcome=list(case.get("expected_outcome", [])),
        )
        for case in data["cases"]
    ]


def _tools(raw: list[str], source: Path) -> tuple[str, ...]:
    if unknown := set(raw) - set(TOOLS):
        raise GoldenError(f"{source}: unknown tools {sorted(unknown)}; use {list(TOOLS)}")
    return tuple(raw)


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
