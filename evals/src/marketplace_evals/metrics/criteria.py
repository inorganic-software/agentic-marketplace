"""Judged criteria: does the agent's work meet each criterion of the golden?

The judge gets the task, every text file in the final workspace (each marked as changed
by the agent or unchanged, so it can also check what should stay as it was), the output
of the golden's `inspect` commands before and after the agent (the git history, say)
and the agent's final message, and answers each criterion yes or no with a reason.
There are no weights: the score is the fraction met, and the run passes if it misses
at most `max_misses`, none of them required, and never if it misses them all.

Two metrics share it, so that each one answers one question:
  - rules_always_met_judged: the golden's `rules_always_met_judged`, the absolute
    prohibitions of the skill that need judgment, shared by every case. All must be met. Judged even if the agent changed nothing: in a trap
    that is the right behavior, and what was already there counts.
  - outcome: `expected_outcome`, what the skill asks for in that case. One miss of an
    optional criterion is tolerated, unless there is a single criterion; a required one
    must be met. The judge is not told which are required: it changes how the answers
    are counted, not the question.
"""

import json

from marketplace_evals.goldens import Criterion, GoldenCase
from marketplace_evals.metrics.base import Check, MetricResult, failed, with_judge_retry
from marketplace_evals.runtimes.judge import Judge, JudgeError
from marketplace_evals.runtimes.process import excerpt
from marketplace_evals.trace import Trace

PROMPT = """An AI coding agent was given a task in a repository. Decide, for each \
criterion below, whether the agent's work meets it.

Rules:
- Judge only the final workspace, the state shown and the agent's final message; \
nothing else exists.
- A criterion is met only if what is shown clearly satisfies it. If what is needed to \
check it is missing, it is not met.
- Judge each criterion on its own, regardless of the others.
- Do not judge style, formatting or anything the criteria do not mention.

Task given to the agent:
{prompt}

Files in the final workspace. Each one is marked "changed by the agent" \
(created or modified) or "unchanged" (as it was before the task):
{files}
{state}
Agent's final message:
{final_output}

Criteria:
{criteria}"""

STATE = """
State before the agent started (output of each command):
{initial_state}

State after the agent finished (output of the same commands):
{final_state}
"""

SCHEMA = {
    "type": "object",
    "properties": {
        "criteria": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "met": {"type": "boolean"},
                    "reason": {"type": "string"},
                },
                "required": ["index", "met", "reason"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["criteria"],
    "additionalProperties": False,
}


def rules_always_met_judged(case: GoldenCase, trace: Trace, judge: Judge) -> MetricResult:
    rules = [Criterion(rule) for rule in case.rules_always_met_judged]  # all must be met: no misses
    return judge_criteria("rules_always_met_judged", rules, 0, case, trace, judge, require_changes=False)


def outcome(case: GoldenCase, trace: Trace, judge: Judge) -> MetricResult:
    return judge_criteria("outcome", case.expected_outcome, 1, case, trace, judge, marks_required=True)


def judge_criteria(
    name: str,
    criteria: list[Criterion],
    max_misses: int,
    case: GoldenCase,
    trace: Trace,
    judge: Judge,
    require_changes: bool = True,
    marks_required: bool = False,  # its checks say whether each is required (`expected_outcome`)
) -> MetricResult:
    # A tolerated miss must leave something to meet: with one criterion, it must be met.
    max_misses = min(max_misses, len(criteria) - 1)
    threshold = (len(criteria) - max_misses) / len(criteria)
    if require_changes and not trace.changed:
        return failed(name, threshold, "the agent changed no file and nothing its golden inspects")
    prompt = PROMPT.format(
        prompt=trace.prompt,
        files=_files(trace) or "(none)",
        state=STATE.format(initial_state=trace.initial_state, final_state=trace.final_state) if case.inspect else "",
        final_output=trace.final_output or "(none)",
        criteria="\n".join(f"{i}. {c.text}" for i, c in enumerate(criteria, 1)),
    )

    def measure() -> MetricResult:
        raw = judge.ask(prompt, json_schema=SCHEMA)
        try:
            answers = {a["index"]: a for a in json.loads(raw)["criteria"]}
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            raise JudgeError(f"invalid answer: {excerpt(raw)}") from e
        if sorted(answers) != list(range(1, len(criteria) + 1)):
            raise JudgeError(f"answer does not cover criteria 1..{len(criteria)}: {excerpt(raw)}")
        checks = [
            Check(
                f"{'[required] ' if c.required else ''}{c.text} — {answers[i]['reason']}",
                bool(answers[i]["met"]),
                key=c.text,
                required=c.required if marks_required else None,
            )
            for i, c in enumerate(criteria, 1)
        ]
        return MetricResult(name, sum(c.passed for c in checks) / len(checks), threshold, checks)

    return with_judge_retry(name, threshold, measure)


def _files(trace: Trace) -> str:
    changed = set(trace.changed_files)
    return "\n\n".join(
        f"--- {path} ({'changed by the agent' if path in changed else 'unchanged'})\n{text}"
        for path, text in trace.final_files.items()
    )
