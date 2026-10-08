"""Whether the skill's description makes the agent load it when it should, and only then.

Scores the golden's `skill_loading` prompts: a `should_load` prompt passes if the agent
loads the skill under evaluation, a `should_not_load` prompt if it does not. Loading it
is calling it by name with the runtime's skill tool or reading its SKILL.md, as a
denied call too, since it counts as attempted.
"""

from marketplace_evals.goldens import GoldenCase
from marketplace_evals.metrics.base import Check, MetricResult
from marketplace_evals.trace import LOAD_SKILL, READ_FILE, ToolCall, Trace


def skill_loading(case: GoldenCase, trace: Trace) -> MetricResult:
    loaded = [c for c in trace.calls if loads_skill(case, c)]
    if case.should_load:
        check = Check(f"loads the skill {case.skill}", bool(loaded), key="loads the skill")
    else:
        detail = f" — got {loaded[0]}" if loaded else ""
        check = Check(f"does not load the skill {case.skill}{detail}", not loaded, key="does not load the skill")
    return MetricResult("skill_loading", float(check.passed), 1.0, [check])


def loads_skill(case: GoldenCase, call: ToolCall) -> bool:
    """Whether `call` loads the case's skill: by name, or by reading its SKILL.md."""
    if call.action == LOAD_SKILL:
        return call.args.get("name") == case.skill
    return call.action == READ_FILE and call.args.get("path") == f"{case.skill_dir}SKILL.md"
