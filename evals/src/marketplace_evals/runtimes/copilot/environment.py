"""An isolated environment for each Copilot CLI session, shared by the runner and judge."""

import json
import os
import re
from pathlib import Path

from marketplace_evals.runtimes.copilot.vertex import Vertex

# The CLI only finds the user's login and network settings through these; any other
# COPILOT_* variable could change the session (model, permissions, extra instructions).
KEPT_VARS = {"COPILOT_GITHUB_TOKEN", "COPILOT_GH_HOST"}

# Keys of config.json that say which `copilot login` to use; its token is in the keychain.
LOGIN_KEYS = ("lastLoggedInUser", "loggedInUsers")

# Where Copilot CLI looks for a GitHub token, in order of precedence.
GITHUB_TOKEN_VARS = ("COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN")


def isolated_env(home: Path, otel_file: Path, model: str, vertex: Vertex | None = None) -> dict[str, str]:
    """Environment for one session: its own telemetry file and a config dir that only
    carries what picks the models.

    On GitHub that is the user's Copilot login: without it, Copilot falls back to the
    `gh` CLI's account, which may be another one with other models enabled. On Vertex
    it is the BYOK provider, and no GitHub login or token reaches the session.
    """
    env = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith(("COPILOT_", "GITHUB_COPILOT_")) or name in KEPT_VARS
    }
    if vertex is None:
        _copy_login(home)
    else:
        for name in GITHUB_TOKEN_VARS:
            env.pop(name, None)
        env |= vertex.env(model)
    env["COPILOT_HOME"] = str(home)
    env["COPILOT_OTEL_EXPORTER_TYPE"] = "file"
    env["COPILOT_OTEL_FILE_EXPORTER_PATH"] = str(otel_file)
    return env


def _copy_login(home: Path) -> None:
    user_home = Path(os.environ.get("COPILOT_HOME", Path.home() / ".copilot"))
    if (user_config := user_home / "config.json").is_file():
        # config.json starts with // comments.
        config = json.loads(re.sub(r"^\s*//.*$", "", user_config.read_text(), flags=re.MULTILINE))
        (home / "config.json").write_text(json.dumps({k: config[k] for k in LOGIN_KEYS if k in config}))
