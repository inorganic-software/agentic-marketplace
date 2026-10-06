"""What an eval session runs with: the provider, the models, N and the minimum."""

import tomllib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from marketplace_evals.paths import MODELS_FILE, PLUGINS_DIR, RUNS_DIR
from marketplace_evals.runtimes import PROVIDERS, Judge, Provider, Runner
from marketplace_evals.runtimes.copilot import Vertex

# Where the models come from: the provider's own service, or Vertex AI (Copilot only).
DEFAULT_BACKEND = "default"
VERTEX_BACKEND = "vertex"
BACKENDS = (DEFAULT_BACKEND, VERTEX_BACKEND)


class ConfigError(ValueError):
    """An impossible combination of options."""


@dataclass(frozen=True)
class EvalConfig:
    provider: str  # key of PROVIDERS
    backend: str  # one of BACKENDS
    model: str  # exact agent model ID
    judge_model: str  # exact judge model ID
    runs: int  # runs per golden case
    min_passes: int  # runs that must pass for a metric to pass the case
    vertex: Vertex | None = None  # with the Vertex backend
    plugins_dir: Path = PLUGINS_DIR
    logs_dir: Path = RUNS_DIR / "unnamed"

    @classmethod
    def create(
        cls,
        provider: str,
        backend: str,
        runs: int,
        min_passes: int,
        model: str | None = None,
        judge_model: str | None = None,
        models_file: Path = MODELS_FILE,
    ) -> EvalConfig:
        """A session's configuration. Without `model` or `judge_model`, the ones pinned
        in models.toml for the provider, or for the backend if it is not the default."""
        if provider not in PROVIDERS:
            raise ConfigError(f"unknown provider {provider!r}; use one of {sorted(PROVIDERS)}")
        vertex = None
        if backend == VERTEX_BACKEND:
            if provider != "copilot":
                raise ConfigError("--backend vertex only works with --provider copilot")
            try:
                vertex = Vertex.from_env()
            except ValueError as e:
                raise ConfigError(str(e)) from e
        pinned = tomllib.loads(models_file.read_text())[backend if vertex else provider]
        return cls(
            provider=provider,
            backend=backend,
            model=model or pinned["agent"],
            judge_model=judge_model or pinned["judge"],
            runs=runs,
            min_passes=min_passes,
            vertex=vertex,
            logs_dir=RUNS_DIR / datetime.now().strftime("%Y%m%d-%H%M%S"),
        )

    @property
    def _provider(self) -> Provider:
        return PROVIDERS[self.provider]

    @property
    def runtime(self) -> str:
        return self._provider.runner.name

    @property
    def judge(self) -> str:
        return self._provider.judge.name

    def new_runner(self) -> Runner:
        return self._provider.runner(model=self.model, logs_dir=self.logs_dir, **self._backend_args())

    def new_judge(self) -> Judge:
        return self._provider.judge(model=self.judge_model, **self._backend_args())

    def _backend_args(self) -> dict:
        """Constructor arguments that pick the backend; none for the provider's own."""
        return {"vertex": self.vertex} if self.vertex else {}
