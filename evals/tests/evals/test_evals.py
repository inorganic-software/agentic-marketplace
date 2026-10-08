import pytest

from marketplace_evals.goldens import discover, load_goldens
from marketplace_evals.metrics import METRICS
from marketplace_evals.paths import GOLDENS_DIR
from marketplace_evals.reporting.terminal import format_case_result

# Every case of every golden under evals/plugins/, so a new golden needs no test file.
CASES = [case for path in discover(GOLDENS_DIR) for case in load_goldens(path)]

# A quality metric of an informative case is a non-strict xfail: XFAIL if it fails,
# XPASS if it passes, and neither fails the session.
INFORMATIVE = pytest.mark.xfail(reason="informative case: it does not fail the session", strict=False)

PARAMS = [
    pytest.param(case, name, id=f"{case.key}-{name}", marks=[INFORMATIVE] if spec.informative(case) else [])
    for case in CASES
    for name, spec in METRICS.items()
    if spec.applies_to(case)
]


@pytest.mark.eval
@pytest.mark.parametrize(("case", "metric_name"), PARAMS)
def test_metric(case, metric_name, eval_session):
    result = eval_session.score(case, metric_name, METRICS[metric_name])
    report = format_case_result(result)
    print("\n" + report)
    if not result.passed:
        pytest.fail(report, pytrace=False)
