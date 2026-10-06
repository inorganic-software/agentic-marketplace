import os
from dataclasses import dataclass

# Prints a fresh access token of the Application Default Credentials: a developer's
# `gcloud auth application-default login`, or Workload Identity Federation in CI.
ADC_TOKEN_COMMAND = "gcloud auth application-default print-access-token"

# Environment variable -> field of Vertex.
ENV_VARS = {"EVALS_VERTEX_PROJECT": "project", "EVALS_VERTEX_LOCATION": "location"}


@dataclass(frozen=True)
class Vertex:
    """Vertex AI as the model provider of Copilot CLI (BYOK).

    Copilot has no Google provider type, so it talks to Vertex's OpenAI-compatible
    endpoint. It asks for a new token on every request, so a long session does not
    outlive it. No GitHub login is needed.
    """

    project: str
    location: str

    @classmethod
    def from_env(cls) -> Vertex:
        if missing := [name for name in ENV_VARS if not os.environ.get(name)]:
            raise ValueError(f"--backend vertex needs {' and '.join(missing)}")
        return cls(**{field: os.environ[name] for name, field in ENV_VARS.items()})

    @property
    def base_url(self) -> str:
        host = "aiplatform.googleapis.com"
        if self.location != "global":
            host = f"{self.location}-{host}"
        return f"https://{host}/v1/projects/{self.project}/locations/{self.location}/endpoints/openapi"

    def env(self, model: str) -> dict[str, str]:
        """Copilot's BYOK provider settings for `model`."""
        return {
            "COPILOT_PROVIDER_TYPE": "openai",
            "COPILOT_PROVIDER_BASE_URL": self.base_url,
            "COPILOT_PROVIDER_API_KEY_COMMAND": ADC_TOKEN_COMMAND,
            "COPILOT_PROVIDER_WIRE_MODEL": model,
        }
