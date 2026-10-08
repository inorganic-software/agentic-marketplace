"""Where the evals find what they evaluate and leave what they produce."""

from pathlib import Path

EVALS_DIR = Path(__file__).resolve().parents[2]
REPO_DIR = EVALS_DIR.parent
PLUGINS_DIR = REPO_DIR / "plugins"  # the plugins under evaluation
GOLDENS_DIR = EVALS_DIR / "plugins"  # their goldens, mirroring them
RUNS_DIR = EVALS_DIR / ".runs"  # one folder per eval session, git-ignored
MODELS_FILE = EVALS_DIR / "models.toml"
