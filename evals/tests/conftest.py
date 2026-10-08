import pytest

from marketplace_evals.config import BACKENDS, DEFAULT_BACKEND, ConfigError, EvalConfig
from marketplace_evals.runtimes import PROVIDERS
from marketplace_evals.session import EvalSession

SESSION = pytest.StashKey[EvalSession]()


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("evals")
    group.addoption(
        "--provider",
        default="claude",
        choices=sorted(PROVIDERS),
        help="Who runs the agent and the judge: Claude Code or Copilot CLI",
    )
    group.addoption(
        "--backend",
        default=DEFAULT_BACKEND,
        choices=BACKENDS,
        help="Where the models come from: the provider's own service, or Vertex AI "
        "(only with --provider copilot; needs EVALS_VERTEX_PROJECT and EVALS_VERTEX_LOCATION)",
    )
    group.addoption(
        "--model",
        default=None,
        help="Exact agent model ID (default: the provider's or backend's in models.toml). "
        "Override only to try a candidate",
    )
    group.addoption(
        "--judge-model",
        default=None,
        help="Exact judge model ID (default: the provider's or backend's in models.toml). "
        "Override only to try a candidate",
    )
    group.addoption("--runs", type=int, default=3, help="Runs per golden case")
    group.addoption("--min-passes", type=int, default=2, help="Runs that must pass for a metric to pass the case")
    group.addoption(
        "--baseline",
        action="store_true",
        help="Also run each case --runs times without its skill, and report what the skill adds "
        "(informative: it never fails a test). Doubles the cost",
    )


@pytest.fixture(scope="session")
def eval_session(pytestconfig: pytest.Config) -> EvalSession:
    option = pytestconfig.getoption
    try:
        config = EvalConfig.create(
            provider=option("--provider"),
            backend=option("--backend"),
            runs=option("--runs"),
            min_passes=option("--min-passes"),
            model=option("--model"),
            judge_model=option("--judge-model"),
            baseline=option("--baseline"),
        )
    except ConfigError as e:
        raise pytest.UsageError(str(e)) from e
    session = pytestconfig.stash[SESSION] = EvalSession(config)
    return session


def pytest_terminal_summary(terminalreporter, config: pytest.Config) -> None:
    session = config.stash.get(SESSION, None)
    if session is None or not session.started:  # no eval ran (e.g. only unit tests)
        return
    summary = session.finish()
    terminalreporter.section("eval efficiency")
    terminalreporter.write_line(summary.efficiency)
    terminalreporter.section("eval usage")
    terminalreporter.write_line(summary.usage)
    if summary.baseline is not None:
        terminalreporter.section("eval baseline")
        terminalreporter.write_line(summary.baseline)
    if summary.skill_loading is not None:
        terminalreporter.section("eval skill loading")
        terminalreporter.write_line(summary.skill_loading)
    if summary.stub_gaps is not None:
        terminalreporter.section("eval stub gaps")
        terminalreporter.write_line(summary.stub_gaps)
    terminalreporter.section("eval results")
    for path in summary.files:
        terminalreporter.write_line(str(path))
