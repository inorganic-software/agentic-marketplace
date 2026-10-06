# marketplace-evals

Behavioral evals for the skills of the plugins in `../plugins/`, designed to be independent of the runtime. They run on Claude Code or on Copilot CLI (`--provider claude|copilot`): the provider runs both the agent and the judge. On Copilot CLI the models can also come from Vertex AI (`--backend vertex`) instead of GitHub Copilot.

```
golden (YAML) ─► Runner (one adapter per runtime) ─► Canonical trace + final workspace ─► metrics ─► pytest
                                                                                          ├─ tool_correctness  (deterministic)
                                                                                          ├─ rules             (LLM judge, per criterion)
                                                                                          └─ outcome           (LLM judge, per criterion)
```

What is evaluated is how the agent works with the plugin loaded (whether it loads the skill on its own, which commands it runs, which it must never run) and what it leaves behind: the files of the workspace and, through each golden's `inspect` commands, any other state, such as the git history.

Only `tool_correctness` is deterministic. Everything about the result is judged by an LLM against criteria written in the golden, so a new skill or a new rule in one only needs golden changes, not code. The cost is that those checks are not exact and add the judge's variability to the agent's.

## Layout: like tests in Java

The evals mirror the plugins, as `src/test` mirrors `src/main`. Each skill has its golden and fixtures at the same path under `evals/`:

```
plugins/commons/skills/git-workflow/SKILL.md                   the skill
evals/plugins/commons/skills/git-workflow/golden.yaml          its cases
evals/plugins/commons/skills/git-workflow/fixtures/<fixture>/  what each case starts from
```

The plugin and the skill come from the golden's path. `uv run pytest` fails if a golden is out of place, points at a plugin, skill, fixture or file that does not exist, or leaves a fixture unused. The evals live outside `plugins/` because clients copy a plugin's whole folder when they install it.

The code, in `src/marketplace_evals/`:

```
paths.py          where the plugins, goldens and runs are
goldens.py        golden cases and their loading; matchers.py, the expected and forbidden calls
trace.py          the canonical vocabulary: tool calls and the trace of a run
sandbox.py        the folder one run touches: workspace, plugin copy, stubs, git isolation
runtimes/         what runs the agent and the judge, one package per runtime
  runner.py         Runner: sandbox -> session -> trace, and the state before and after
  judge.py          Judge: an LLM with no tools that answers the metrics' questions
  canonical.py      a runtime's tool call in the canonical vocabulary
  claude_code/      runner, judge and output parser of Claude Code
  copilot/          runner, judge, output parser, isolated environment and Vertex backend of Copilot CLI
metrics/          tool_correctness, rules and outcome, and which cases each one scores
evaluation.py     N runs per case, scored by each metric
config.py         what a session runs with: provider, backend, models, N and the minimum
session.py        a session's runs and results, and the files it leaves
reporting/        terminal report, results.json, the PR comment and the comparison page
```

- `runtimes/claude_code/` runs `claude -p` and `runtimes/copilot/` runs `copilot -p`, for the agent and for the judge. The judge runs with no tools, from an empty folder.
- The metrics:
  - `tool_correctness`: expected calls (loading the skill, a command the skill prescribes) and forbidden calls (a command the skill forbids, with `match` and `not_match`).
  - `rules`: the judge answers each of the golden's `rules`, the skill's absolute prohibitions. Shared by every case, traps included. Every rule must be met. It is judged even if the agent changed nothing, and what was already there counts too.
  - `outcome`: the judge answers each `expected_outcome` criterion yes or no, with a reason. No weights. Passes with at most one miss. Fails without judging if the agent changed no file and nothing its golden inspects.
  - If the judge fails, it is retried once. If it fails again, that run does not pass.
- `uv run evals-report <results.json>`: the Markdown report of a session that CI posts on the PR.
- `uv run evals-compare [folder | session | results.json ...] [-o out.html] [--open]`: a self-contained HTML page comparing sessions side by side, by default every session in `.runs/`, written to `.runs/compare.html`. You pick the sessions from a dropdown in each empty column. For each golden case and metric it shows the passed runs out of N and the mean score, unfolding into its checks; rows where the sessions differ are highlighted. Nothing says whether a difference is significant: with N=3 one run moves the rate a lot. A check reworded in the golden is a new row, since its text is its key.
- `models.toml`: the exact agent and judge model IDs, one section per provider and one for the Vertex backend. Every run checks the runtime really used them.
- `.runs/<date>/`: one folder per eval session, git-ignored:
  - `<run>.jsonl`, `<run>/` and `<run>.state.txt`: the raw log, the final files and the `inspect` output before and after, of each run.
  - `results.json`: the session's structured results, to compare sessions over time (a skill change, a new model). For each case, metric and check, how many runs passed it, under a key that does not change between sessions. It also records the runtime and its version, the backend, the models, N and the minimum, the repo's commit and whether `plugins/` or `evals/` had uncommitted changes, and the usage.
  - `summary.txt`: what pytest prints, which is lost when the terminal closes: every report with its checks and the judge's reasons, then the turns and usage tables.

## Goldens

One `golden.yaml` per skill. The format is in `src/marketplace_evals/goldens.py`, and `plugins/commons/skills/git-workflow/golden.yaml` is a full example:

- `tools`: what the agent may use, in Copilot's agent vocabulary (`read`, `edit`, `search`, `execute`). Each runner translates it. Loading skills is always allowed.
- `inspect`: shell commands run in the workspace before and after the agent. The judge sees both outputs, for what a file snapshot does not show (a commit changes no file in the working tree).
- `rules`: absolute prohibitions, judged in every case.
- `cases`: each with `id`, `fixture`, `prompt`, and optionally `expected_calls`, `forbidden_calls` and `expected_outcome`.

Calls are written in the canonical vocabulary (`read_file`, `load_skill`, `shell`, `edit_file`, `write_file`...). A skill loaded with the runtime's skill tool is `load_skill` with the skill's name, without the plugin's prefix (Claude Code says `commons:git-workflow`, Copilot `git-workflow`). Paths are relative to the workspace, and a file of the plugin is `@plugins/<plugin>/...`.

Write the prompts as a user would, without naming the skill: a case then also checks that the skill's description makes the agent load it.

### Fixtures

A fixture is a folder copied as the agent's workspace. If it has a `setup.sh`, it runs in the workspace before the agent and is not copied. It can build what a plain folder cannot hold, such as a git repository with its history (a fixture cannot contain a `.git` folder).

## How a run works

Each case runs N times, each time in its own sandbox, an empty temporary folder:

1. The fixture is copied to `workspace/`, the plugin under evaluation to `plugins/<plugin>/`, and the fixture's `setup.sh` runs in the workspace.
2. The sandbox gets its own environment: `bin/` first in the `PATH`, for stubs the fixture installs (for example, a `gh` that never reaches GitHub), its own git global configuration instead of yours (no hooks, signing or aliases of yours, a fixed identity), and `EVAL_SANDBOX` pointing at the sandbox, for setup scripts and stubs.
3. The golden's `inspect` commands run, and the workspace's files are recorded.
4. The session runs in the workspace with only that plugin loaded, through `--plugin-dir` on both runtimes, and the prompt. No sandbox beyond that: the agent's tools run on the machine, shell included.
5. The log becomes a canonical trace. The workspace's path is removed, the plugin's copy becomes `@plugins/` and the rest of the sandbox `@sandbox/`.
6. `inspect` runs again, and the final files go to the metrics with which of them the agent created or modified. Everything is saved next to the log, since the sandbox is deleted.

### With `--provider claude`

`claude -p "<prompt>" --plugin-dir <plugin> --model <id>`, with `--setting-sources project`, `--strict-mcp-config` and `--no-session-persistence`, so no user settings, plugins, hooks or MCPs reach it. The golden's tools become `--allowedTools` (`read` → Read, `edit` → Edit + Write, `search` → Grep + Glob, `execute` → Bash, plus Skill) with `--permission-mode dontAsk`: anything else is denied without asking and stays in the trace as denied. Checked on Claude Code 2.1.289.

### With `--provider copilot`

Checked on Copilot CLI 1.0.91:

- `copilot -p "<prompt>" --plugin-dir <plugin> --model <id> --output-format json`. Each run gets an empty `COPILOT_HOME` that only carries your `copilot login` (the `lastLoggedInUser`/`loggedInUsers` entries of `~/.copilot/config.json`; the token stays in the keychain), and no `COPILOT_*` variables except the login ones, so nothing else from your own configuration reaches it, plus `--disable-builtin-mcps` and `--no-ask-user`.
- Permissions come from the golden's `tools`: `read`/`search` → `read`, `edit` → `write`, `execute` → `shell`, passed as `--allow-tool`. Anything else is denied without asking and stays in the trace as denied. Copilot auto-approves read-only shell commands.
- The trace comes from the JSONL events (`tool.execution_start`/`complete`, `assistant.message`). `apply_patch` becomes one `edit_file` per updated file and one `write_file` per new file. Tokens come from an OpenTelemetry file export of the same session (the JSONL has none), and AI credits from its last `session.usage_checkpoint`.
- Copilot exits with code 0 even when it cannot start (for example, a model the account cannot use): a run without a `result` event fails with the CLI's error.
- Copilot runs `gh auth token` itself at startup. A fixture's `gh` stub must not log it as the agent's work: see `fixtures/common.sh` of `git-workflow`.
- Not verified: delegation. No skill evaluated so far delegates, so every call is attributed to `main`.
- The judge runs with no tools (`--available-tools ask_user --no-ask-user`) and no custom instructions, with the prompt on stdin. Copilot has no `--json-schema`, so the schema goes in the prompt and the answer must parse as JSON.

### With `--provider copilot --backend vertex`

The same runner and judge, with the models of Vertex AI instead of GitHub Copilot's, through Copilot's BYOK provider:

- Copilot has no Google provider type, so it uses `COPILOT_PROVIDER_TYPE=openai` against Vertex's OpenAI-compatible endpoint. `openai` is only the wire format: the requests go to Vertex, in your project, and are billed there.
- The project and location come from `EVALS_VERTEX_PROJECT` and `EVALS_VERTEX_LOCATION`, so nothing about GCP is in the repo. Some models are only served in the `global` location; the regional endpoints answer 404.
- Authentication is your Application Default Credentials: Copilot runs `gcloud auth application-default print-access-token` before every request, so a long session never outlives its token. In CI, Workload Identity Federation provides the same credentials.
- No GitHub login: the session gets neither your `copilot login` nor `COPILOT_GITHUB_TOKEN`, `GH_TOKEN` or `GITHUB_TOKEN`, so no model can come from GitHub.
- Vertex reports reasoning tokens apart from output tokens, so they get their own column. There are no AI credits, and the usage table leaves out the cost and credit columns when nothing reports them.

Each golden case runs N times, only once, and each metric is a pytest test that scores those same runs. A metric passes the case if at least `--min-passes` of the `--runs` runs pass it.

## Requirements

| What | Version | Notes |
|---|---|---|
| **uv** | 0.12.x | `pyproject.toml` requires `uv_build>=0.12.19,<0.13` |
| **Python** | 3.14 | Pinned in `.python-version`. If it is missing, `uv sync` downloads it |
| **git** and **bash** | | The fixtures' setup scripts and the `inspect` commands use them |

For the real evals (`-m eval`) also, with `--provider claude` (the default):

| What | Notes |
|---|---|
| **Claude Code CLI** (`claude`) on the `PATH` | Runs the agent and the judge. Tested with **2.1.289** |
| **Logged-in Claude Code session** | A subscription (OAuth) for now |
| **Access to the pinned models** | Those in `[claude]` in `models.toml` |

With `--provider copilot`:

| What | Notes |
|---|---|
| **Copilot CLI** (`copilot`) on the `PATH` | Runs the agent and the judge. Tested with **1.0.91** |
| **Logged-in Copilot CLI** | `copilot login` (keychain) or `COPILOT_GITHUB_TOKEN` / `GH_TOKEN` |
| **Access to the pinned models** | Those in `[copilot]` in `models.toml`, enabled by your Copilot policy |

With `--provider copilot --backend vertex`, instead of the Copilot login and policy:

| What | Notes |
|---|---|
| **Copilot CLI** (`copilot`) on the `PATH` | Tested with **1.0.91** |
| **gcloud CLI** with Application Default Credentials | `gcloud auth application-default login` |
| **`roles/aiplatform.user`** on the project | And the Vertex AI API enabled there |
| **`EVALS_VERTEX_PROJECT`, `EVALS_VERTEX_LOCATION`** | Your project, and the location that serves the model in `[vertex]` |

## Usage

```sh
uv sync --locked            # installs exactly what uv.lock says
uv run pytest               # unit tests and golden checks (no LLM)
uv run ruff check && uv run ruff format --check   # lint and format
uv run pytest -m eval -s    # real evals: agent N times + judge, on Claude Code
uv run pytest -m eval -s --provider copilot   # the same on Copilot CLI
EVALS_VERTEX_PROJECT=<project> EVALS_VERTEX_LOCATION=global \
  uv run pytest -m eval -s --provider copilot --backend vertex   # Copilot CLI with Vertex AI models
uv run pytest -m eval -s --runs 5 --min-passes 4 -k git-workflow   # one skill
uv run pytest -m eval -s -k "commons/git-workflow/open-pr-"         # one case
uv run pytest -m eval -s --model <candidate-model-id>   # try a new model against the pinned one
```

### Adding a case or a skill

1. Add the fixture in `evals/plugins/<plugin>/skills/<skill>/fixtures/<fixture>/`, with a `setup.sh` if it needs one.
2. Add the case to that skill's `golden.yaml`, or create the golden. No test file is needed: `tests/evals/test_evals.py` runs every golden it finds.
3. `uv run pytest` checks that the golden is in place and points at things that exist.
4. `uv run pytest -m eval -s -k <plugin>/<skill>/<case-id>`.

## CI

Two workflows in `../.github/workflows/`:

- **`evals-unit-tests.yml`** (`evals unit tests` check): `ruff` and `uv run pytest` on every PR, not only those touching `evals/`, since it also checks the goldens against the plugins. It is meant to be a required check, and a required check whose workflow never starts stays pending and blocks the merge. It takes under a minute and calls no LLM.
- **`evals.yml`** (`evals` check): the real evals, `--provider copilot --backend vertex --runs 3 --min-passes 2`, when a PR changes `plugins/**`, `evals/**` or the workflow itself. It is informative: a case below the minimum turns it red, but it is not required. A new push cancels the run of the previous one. PRs from forks skip it, since they cannot sign in to Google Cloud. It leaves:
  - a PR comment with each case's metrics and the usage (`uv run evals-report`), updated on every push;
  - the same report in the job summary, plus `summary.txt` with the judge's reasons;
  - the `evals-runs` artifact: `.runs/`, with the log, final files and state of every run.

It signs in to Google Cloud without keys, through Workload Identity Federation, as a service account that can only call Vertex AI (`roles/aiplatform.user`). The workflow reads everything about GCP from repo variables, so none of it is in this public repo: `GCP_WIF_PROVIDER`, `GCP_EVALS_SERVICE_ACCOUNT`, `EVALS_VERTEX_PROJECT` and `EVALS_VERTEX_LOCATION`. Copilot CLI is pinned in the workflow; change it there and in the requirements above together.
