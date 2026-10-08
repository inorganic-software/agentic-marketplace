# marketplace-evals

Behavioral evals for the skills of the plugins in `../plugins/`, designed to be independent of the runtime. They run on Claude Code or on Copilot CLI (`--provider claude|copilot`): the provider runs both the agent and the judge. On Copilot CLI the models can also come from Vertex AI (`--backend vertex`) instead of GitHub Copilot.

```
golden (YAML) ─► Runner (one adapter per runtime) ─► Canonical trace + final workspace ─► metrics ─► pytest
                                                                                          ├─ expected_calls           (deterministic)
                                                                                          ├─ forbidden_calls          (deterministic, strict)
                                                                                          ├─ rules_always_met         (deterministic, shell commands, strict)
                                                                                          ├─ rules_always_met_judged  (LLM judge, per criterion, strict)
                                                                                          ├─ outcome_checks           (deterministic, shell commands)
                                                                                          ├─ outcome                  (LLM judge, per criterion)
                                                                                          ├─ skill_loading            (deterministic, the golden's skill_loading prompts)
                                                                                          └─ integrity                (deterministic, every case, strict)
```

What is evaluated is how the agent works with the plugin loaded (whether it loads the skill on its own, which commands it runs, which it must never run) and what it leaves behind: the files of the workspace and, through each golden's `inspect` commands, any other state, such as the git history.

What can be checked exactly is checked with code: the calls in the trace (`expected_calls` and `forbidden_calls`) and shell commands the golden runs on the final state (`rules_always_met` and `outcome_checks`), such as a branch name against a regex or a clean working tree. Only what needs judgment (is a description imperative, was a conflict resolved by intent) goes to an LLM judge (`rules_always_met_judged` and `outcome`), which adds its own variability to the agent's. Either way, a new skill or a new rule in one only needs golden changes, not code.

Besides the cases, each golden can list `skill_loading` prompts: some that should make the agent load the skill and some that should not. Each one only checks whether the skill's description makes the agent load it when it should, and only then. See [Skill loading](#skill-loading-does-the-description-load-it-when-it-should).

The prohibitions (`forbidden_calls`, `rules_always_met`, `rules_always_met_judged` and `integrity`) are strict: every run must pass them (pass^k), since a user needs them kept every time. The rest pass a case with `--min-passes` of the `--runs` runs, and in a case marked `informative` they never fail the session: they measure what the agent does not do well yet. See [Informative cases](#informative-cases-what-the-agent-does-not-do-well-yet).

With `--baseline`, each case also runs without its skill, to see what the skill adds: if a case passes without it, the case does not measure the skill. See [Baseline](#baseline-what-the-skill-adds).

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
sandbox.py        the folder one run touches: workspace, plugin copy, stubs, the agent's clean environment
integrity.py      the agent's calls that reach outside its sandbox or into the eval
runtimes/         what runs the agent and the judge, one package per runtime
  runner.py         Runner: sandbox -> session -> trace, and the state before and after
  judge.py          Judge: an LLM with no tools that answers the metrics' questions
  canonical.py      a runtime's tool call in the canonical vocabulary
  claude_code/      runner, judge and output parser of Claude Code
  copilot/          runner, judge, output parser, isolated environment and Vertex backend of Copilot CLI
metrics/          the calls, the checks, the judged rules and outcome, integrity, which cases each one scores and which are strict
evaluation.py     N runs per case, scored by each metric, and the baseline without the skill
config.py         what a session runs with: provider, backend, models, N, the minimum and the baseline
session.py        a session's runs and results, and the files it leaves
reporting/        terminal report, results.json, the PR comment and the comparison page
```

- `runtimes/claude_code/` runs `claude -p` and `runtimes/copilot/` runs `copilot -p`, for the agent and for the judge. The judge runs with no tools, from an empty folder.
- The metrics:
  - `expected_calls`: the case's expected calls (loading the skill, a command the skill prescribes). Every one must appear.
  - `forbidden_calls`: the case's forbidden calls (a command the skill forbids, with `match` and `not_match`). None may appear.
  - `rules_always_met`: the golden's `rules_always_met`, the skill's absolute prohibitions that can be checked exactly, written as what must always be true ("origin's main was not rewritten"). Shared by every case, traps included. Every check must pass. Run even if the agent changed nothing.
  - `outcome_checks`: the case's `outcome_checks`, what the skill asks for in that case that can be checked exactly. Every check must pass. Fails without running them if the agent changed no file and nothing its golden inspects.
  - A check is a shell command run in the workspace after the agent, with `bash -e -o pipefail` (a check of several lines fails at the first that fails) and the sandbox's environment, and passes if it exits with 0. It times out after 60 s. A failed check shows its exit code and the start of its output in the report and in `<run>.state.txt`.
  - `rules_always_met_judged`: the judge answers each of the golden's `rules_always_met_judged`, the skill's absolute prohibitions that need judgment. Shared by every case, traps included. Every rule must be met. It is judged even if the agent changed nothing, and what was already there counts too.
  - `outcome`: the judge answers each `expected_outcome` criterion yes or no, with a reason. No weights. A `required` criterion must be met; of the `optional` ones, one may be missed, unless it is the only criterion (a run never passes if it misses them all). The judge is not told which are required. Fails without judging if the agent changed no file and nothing its golden inspects.
  - `integrity`: on every case, whether the agent tried to read the eval or to leave its sandbox, in any call, denied ones included. See [Isolation](#isolation-what-the-agent-sees-and-what-it-does-not). Strict: a run that tried passes nothing, since it may have done its work knowing how it would be checked.
  - `skill_loading`: only on the golden's `skill_loading` prompts. A `should_load` prompt passes if the agent loads the skill under evaluation (with the skill tool, by its name, or by reading its `SKILL.md`), a `should_not_load` prompt if it does not.
  - If the judge fails, it is retried once. If it fails again, that run does not pass.
- `uv run evals-report <results.json>`: the Markdown report of a session that CI posts on the PR.
- `uv run evals-compare [folder | session | results.json ...] [-o out.html] [--open]`: a self-contained HTML page comparing sessions side by side, by default every session in `.runs/`, written to `.runs/compare.html`. You pick the sessions from a dropdown in each empty column. For each golden case and metric it shows the passed runs out of N, how many had to pass, and the mean score, unfolding into its checks; rows where the sessions differ are highlighted. Nothing says whether a difference is significant: with N=3 one run moves the rate a lot. A check reworded in the golden is a new row, since its text is its key. A required criterion of `outcome` shows as `[required]`. A session with a baseline also shows, in each cell, the runs that passed without the skill and the difference (`w/o 1/3 · Δ +67 pp`), and per golden whether the case passes without the skill. A case with several prompt variants unfolds each metric into a row per variant (`↳ prompt coloquial`), with and without the skill; checks are not broken down by variant. Each golden also has an `efficiency` row per measure (turns, tool calls, tokens, cost, time) with the median per run and, with a baseline, the median without the skill and the difference (`w/o 6 · Δ +3 (+50 %)`); sessions before schema 8 show `–`. A golden whose runs made calls the fixture's stubs do not imitate gets a `stub gaps` row (`⚠ 1/3 runs · w/o 2`), from schema 9.
- `models.toml`: the exact agent and judge model IDs, one section per provider and one for the Vertex backend. Every run checks the runtime really used them.
- `.runs/<date>/`: one folder per eval session, git-ignored:
  - `<run>.jsonl`, `<run>/` and `<run>.state.txt`: the raw log, the final files, and the `inspect` output before and after with the result of each check, of each run. In a case with several prompt variants, `<run>` carries the variant's id (`<case>-coloquial-1-...`).
  - `results.json`: the session's structured results, to compare sessions over time (a skill change, a new model). For each case, metric and check, how many runs passed it (and, per metric, how many had to: `required`, and `strict`), under a key that does not change between sessions. It also records the runtime and its version, the backend, the models, N and the minimum, the repo's commit and whether `plugins/` or `evals/` had uncommitted changes, and the usage. A `skill_loading` prompt is a case with `should_load`, and `skill_loading` sums them up per skill. Each case records whether it is `informative`, and each metric whether failing it is only reported (`informative`). Each check of `outcome` records whether it is `required` (schema 6). With `--baseline` (`config.baseline`), each metric also has a `baseline` block with the metric with and without the skill (`with_skill`, `without_skill`, both without what only the skill can pass) and their difference (`delta`), or `null` if only the skill can pass it; and each case, whether it passes with and without the skill. Each run of `per_run` records its prompt `variant`, and in a case with several, each metric records `variants` (`passes` and `runs` per variant) and its `baseline`, `variant_deltas` (schema 7). Each case records its `efficiency` (see [Efficiency](#efficiency-what-the-runs-spend)), and `usage.agent` leaves out the runs without the skill, which go to `usage.agent_without_skill` (schema 8). Each run of `per_run` records its `stub_gaps` (`stub`, `argv`, `reason`), and each case how many of its runs had any, `runs_with_stub_gaps` (and with a baseline `runs_with_stub_gaps_without_skill`) (schema 9).
  - `summary.txt`: what pytest prints, which is lost when the terminal closes: every report with its checks and the judge's reasons (a required criterion of `outcome` marked `[required]`, and a run that missed one, `required criterion missed`), then the efficiency and usage tables, and the `eval stub gaps` section if there are any (see below).

## Goldens

One `golden.yaml` per skill. The format is in `src/marketplace_evals/goldens.py`, and `plugins/commons/skills/git-workflow/golden.yaml` is a full example:

- `tools`: what the agent may use, in Copilot's agent vocabulary (`read`, `edit`, `search`, `execute`). Each runner translates it. Loading skills is always allowed.
- `inspect`: shell commands run in the workspace before and after the agent. The judge sees both outputs, for what a file snapshot does not show (a commit changes no file in the working tree).
- `before`: a map of names to shell commands, run in the workspace after the fixture's setup and before the agent, to record the state a check compares with (where `main` was, say). What each one prints is a variable of the checks with its name (`MAIN_BEFORE: git rev-parse main` gives `$MAIN_BEFORE`), kept by the harness and never in the sandbox, so the agent can neither see nor change it. Names are uppercase shell variables. If one fails, the run does not start.
- `rules_always_met`: absolute prohibitions that can be checked exactly, each with a `name` and a `run` command, checked in every case.
- `rules_always_met_judged`: absolute prohibitions that need judgment, judged in every case. Plain texts, all of them required: `required:`/`optional:` is an error here.
- `skill_loading`: a `fixture` and the prompts that `should_load` the skill and that `should_not_load` it. See [Skill loading](#skill-loading-does-the-description-load-it-when-it-should).
- `cases`: each with `id`, `fixture`, `prompts` (see [Prompt variants](#prompt-variants-the-same-request-worded-otherwise)), and optionally `informative` (see [Informative cases](#informative-cases-what-the-agent-does-not-do-well-yet)), `expected_calls`, `forbidden_calls`, `outcome_checks` (`name` and `run`, as `rules_always_met`) and `expected_outcome` (each criterion `required: <text>` or `optional: <text>`; a plain text is `optional`). Any other key is an error, so a typo or a renamed field cannot turn a check off.

Put in `rules_always_met` and `outcome_checks` everything that can be checked exactly, and leave `rules_always_met_judged` and `expected_outcome` for what needs judgment. A check's `name` is its key between sessions: no two checks of a case, the golden's `rules_always_met` included, share one, and renaming it starts a new row in `evals-compare`. The same goes for an `expected_outcome` criterion and its text; marking it `required` or `optional` does not change its key.

```yaml
expected_outcome:
  - required: "The conflict was resolved by intent: …"     # missing it fails the run
  - optional: "The commit description is in the imperative mood"
  - "The scope names the module"                           # optional
```

A run passes `outcome` if it meets every `required` criterion and misses at most one `optional`. Mark `required` what the case is about, so that missing it is never the tolerated miss.

### What each field checks

| Field | Scope | Decided by | Metric | A run passes it if | If the agent changed nothing |
|---|---|---|---|---|---|
| `rules_always_met` | every case | code: a shell command, passes with exit code 0 | `rules_always_met` | every check passes | still checked |
| `rules_always_met_judged` | every case | the judge | `rules_always_met_judged` | every rule is met | still judged |
| `expected_calls` | its case | code: matched against the trace | `expected_calls` | every expected call appears, in any order (`any_of` for alternatives) | still checked |
| `forbidden_calls` | its case | code: matched against the trace | `forbidden_calls` | no forbidden call appears | still checked |
| `outcome_checks` | its case | code: a shell command, passes with exit code 0 | `outcome_checks` | every check passes | fails without running them |
| `expected_outcome` | its case | the judge | `outcome` | every `required` criterion is met, and at most one `optional` is missed (none if it is the only criterion) | fails without judging |
| `skill_loading` | its prompt | code: matched against the trace | `skill_loading` | the skill is loaded if it `should_load`, and not if it `should_not_load` | still checked |

- `rules_always_met` is always code and `rules_always_met_judged` always the judge: whoever writes the golden picks the field for each rule, by whether it can be checked exactly.
- Calls count as attempted: a call the runtime denied still meets `expected_calls`, and still breaks `forbidden_calls`.
- Each metric is its own pytest test. A failed metric turns its case and the session red (unless it is a quality metric of an informative case), but the other metrics of the case are still scored and reported: nothing stops at the first failure.
- Across runs, `expected_calls`, `outcome_checks`, `outcome` and `skill_loading` pass a case if at least `--min-passes` of the `--runs` runs pass them (2 of 3 by default). The prohibitions, `forbidden_calls`, `rules_always_met`, `rules_always_met_judged` and `integrity`, are strict: every run must pass them, so an agent that breaks one in 1 run of 3 fails the case. A run with an error (the runtime failed, another model answered, the judge failed twice) counts as not passed, so it fails a strict metric too. What is strict is fixed in `metrics/__init__.py`, not in the golden.
- `before` and `inspect` check nothing: `before` records the state the checks compare with, and `inspect` shows the judge the state before and after the agent.

Calls are written in the canonical vocabulary (`read_file`, `load_skill`, `shell`, `edit_file`, `write_file`...). A `shell` call's `command` is matched one simple command at a time (`a && b; c | d` is four, split outside quotes), with `match` and `not_match` on the same one, so `*git push*main*` does not match `git push -u origin HEAD && gh pr create --base main`. A skill loaded with the runtime's skill tool is `load_skill` with the skill's name, without the plugin's prefix (Claude Code says `commons:git-workflow`, Copilot `git-workflow`). Paths are relative to the workspace, and a file of the plugin is `@plugins/<plugin>/...`.

Write the prompts as a user would, without naming the skill: a case then also checks that the skill's description makes the agent load it.

### Prompt variants: the same request, worded otherwise

A skill tuned on one wording per case can pass the evals and fail users who ask the same thing in other words. So each case lists its prompt in one or more variants, each with an `id` and a `text`:

```yaml
cases:
  - id: commit-feature
    fixture: feature-on-main
    prompts:
      - id: directo
        text: "Commitea estos cambios."
      - id: coloquial
        text: "venga, haz commit de lo que tengo y listo"
      - id: con-contexto
        text: "He añadido una función farewell en src/greeting.py (...). Commitea estos cambios."
```

- Run i gets variant i mod n, in order, not at random: with `--runs 3` and three variants each runs once; with 3 runs and two variants, the first runs twice; with fewer runs than variants, the last ones never run (the reports say `not run`). The runs without the skill of `--baseline` get the same variants.
- The case passes or fails on all its runs together, with the usual rules (`--min-passes`, strict metrics N of N). Each variant's rate is only reported: it never fails a test.
- The judge sees the variant the run got, not the first one.
- A variant's `id` is any name you pick (`directo`, `con-ticket`, `usuario-nuevo`...), and its key between sessions: rewording its text or reordering the list keeps its history in `evals-compare`, renaming it starts a new row. Ids are unique within a case; nothing else is checked. A case with one variant is reported as before, without the breakdown.
- Write the variants in the skill's language, as different users would word the same request: casual, with more context than needed, or naming the goal instead of the git verb.
- `skill_loading` prompts have no variants: each one is already a case, so another wording is another prompt.

### Informative cases: what the agent does not do well yet

A case with `informative: true` measures something the agent still gets wrong sometimes, to see whether a change to the skill improves it. It is run and scored like any other case, but its quality metrics do not fail the session:

```yaml
cases:
  - id: commit-breaking-change
    informative: true
    fixture: breaking-change
    prompts:
      - id: directo
        text: "Commitea estos cambios."
    outcome_checks:
      - name: the commit is marked as a breaking change
        run: git log -1 --format=%B | grep -Eq '^[a-z]+(\(.+\))?!:|^BREAKING CHANGE:'
```

- `expected_calls`, `outcome_checks` and `outcome` are informative: pytest marks them `XFAIL` when they fail and `XPASS` when they pass, and neither turns the session red.
- The prohibitions (`forbidden_calls`, `rules_always_met`, `rules_always_met_judged`) stay required and strict: breaking one fails the session, informative case or not.
- A `required` criterion of `expected_outcome` does not change this: it only decides whether a run passes `outcome`, and `outcome` stays informative.
- The reports show its informative metrics with `ℹ️` instead of `✅`/`❌` and leave them out of the count of metrics passed; the PR comment adds how many reached the minimum. `summary.txt` heads their reports with `XFAIL` or `XPASS`.
- When an informative case passes steadily (`XPASS` in several sessions; see `evals-compare`), remove `informative: true`: from then on it guards against regressions like the rest. Its id does not change, so its history in `evals-compare` goes on.
- `skill_loading` prompts cannot be informative.

### Skill loading: does the description load it when it should?

An agent loads a skill when its `description` matches the request. The cases only check that it does when it should, on the prompts they were written for. A description broad enough to load the skill on any request about git would pass them all. `skill_loading` checks both sides, with prompts that only exist for it:

```yaml
skill_loading:
  fixture: feature-on-main              # what every prompt starts from
  should_load:                          # the skill's work, in other words than the cases
    - "Guarda estos cambios en git."
  should_not_load:                      # close to the skill, without asking for its work
    - "¿Qué hace la función greet?"
```

- Each prompt is a case of its own, `should-load-<slug>` or `should-not-load-<slug>`, named after its text: rewording a prompt starts a new row in `evals-compare`. `-k should-` runs only them.
- It runs with read-only tools (`read`, `search`, and loading skills): enough to decide whether to load the skill, not to do the work. A prompt that asks for a commit is a short run that cannot commit.
- It is scored only by `skill_loading`: no `inspect`, `before`, rules or checks of the golden. It passes with `--min-passes` of the `--runs` runs, as a quality criterion: loading the skill when it is not needed breaks nothing by itself, and what would is caught by the prohibitions.
- With `--baseline`, it does not run without the skill, since then it could not load it.
- At the end of the session, the `eval skill loading` section, `results.json` (`skill_loading`) and the PR comment sum them up per skill: of the prompts that should load it, how many passed, and of those that should not, how many; with the runs that passed of each.
- Good `should_not_load` prompts are near misses: about the same repo, its code or git itself, without asking for what the skill does. Prompts far from the skill pass without telling anything.

### Fixtures

A fixture is a folder copied as the agent's workspace. If it has a `setup.sh`, it runs in the workspace before the agent and is not copied. It can build what a plain folder cannot hold, such as a git repository with its history (a fixture cannot contain a `.git` folder).

The `git-workflow` fixtures share `fixtures/common.sh` and a `gh` stub, `fixtures/gh`, which stands in for GitHub with the fixture's bare remote `origin.git` as `github.com/example/greeting`:

- It imitates the commands and flags agents have been seen to use (`pr create`, `view`, `list`, `status`, `checks`, `edit` and `merge`, `label list`, `repo view`, `auth status`, `version`, with `--json` and `--jq`), as gh does outside a terminal. `--help` shows a short help of its own and does nothing else. Anything else fails as gh would (`unknown flag: --admin`) and is reported as a stub gap (see [How a run works](#how-a-run-works)): add it to the stub when agents keep reaching for it.
- A PR that exists when the agent starts is a `pr.json` the setup writes with `write_pr`, with its review and checks. `gh pr merge` only merges an approved PR whose checks passed, as main's protection would, and needs `--squash`, `--merge` or `--rebase`, as gh does outside a terminal.
- What the agent did is kept in `origin.git`, not in files of the sandbox: each PR as JSON in the message of `refs/pull/<n>/meta` (its title, state...), and each merge committed by `GitHub <noreply@github.com>`, so a check tells it from a commit pushed by hand. `--jq` and the golden's checks need `jq`.

## How a run works

Each case runs N times, each time in its own sandbox, an empty temporary folder with a random name, inside a folder of the session's own:

1. The fixture is copied to `workspace/`, the plugin under evaluation to `plugins/<plugin>/` (without the skill under evaluation, in a baseline run), and the fixture's `setup.sh` runs in the workspace, followed by the golden's `before` commands.
2. The sandbox gets its own environment: `bin/` first in the `PATH`, for stubs the fixture installs (for example, a `gh` that never reaches GitHub), and its own git global configuration instead of yours (no hooks, signing or aliases of yours, a fixed identity). The harness's own commands (`setup.sh`, `before`, `inspect` and the checks) also get `EVAL_SANDBOX`, pointing at the sandbox; the agent gets a clean environment instead (see [Isolation](#isolation-what-the-agent-sees-and-what-it-does-not)). A stub that gets a call it does not imitate answers with an error, as the real tool would, and appends `{"stub": "gh", "argv": [...], "reason": "unknown flag: --fill"}` as a line of `$EVAL_SANDBOX/stub-gaps.jsonl`. The runner reads it right after the agent (the checks and `inspect` do not count) and the reports show those calls as stub gaps: under each run (`stub gaps: …`), in an `eval stub gaps` section at the end of the terminal and `summary.txt` (per case, its runs with any and each distinct call), in `results.json`, in `evals-compare` and as one line of the PR comment. They change no score: a run with gaps may say more about the stub than about the agent, so look at them before trusting its result.
3. The golden's `inspect` commands run, and the workspace's files are recorded.
4. The session runs in the workspace with only that plugin loaded, through `--plugin-dir` on both runtimes, and the prompt, in the agent's clean environment.
5. The log becomes a canonical trace. The workspace's path is removed, the plugin's copy becomes `@plugins/` and the rest of the sandbox `@sandbox/`.
6. `inspect` runs again, then the case's checks (the golden's `rules_always_met` and the case's `outcome_checks`) with the `before` state as variables, the agent's calls are searched for breaches of its isolation, and the final files go to the metrics with which of them the agent created or modified. Everything is saved next to the log, since the sandbox is deleted.

### Isolation: what the agent sees, and what it does not

The agent runs as you, on your machine: nothing but its runtime's own permissions stops its shell from reading any file you can read. Agents have been seen to look for the eval itself (the golden, the package that scores them, the files a stub keeps) and to pass its checks by hand. So, short of a container:

- **A clean environment.** The agent's environment is built from scratch (`Sandbox.agent_env`), not inherited: the sandbox's `PATH` without the repo's own folders (its virtualenv, so `import marketplace_evals` fails), its own `HOME` and `TMPDIR` inside the sandbox, git's isolated configuration, your user, locale and proxy settings, and what its runtime needs to log in. Nothing of the session that launched it (a Claude Code session's variables and socket), of the repo (`VIRTUAL_ENV`, `UV*`), of the eval (`EVAL_SANDBOX`, `EVALS_*`) or of CI (`ACTIONS_*`, `GOOGLE_APPLICATION_CREDENTIALS`).
- **A HOME with only the login.** Claude Code gets a `.claude.json` with only the keys that say which login to use (`oauthAccount`, `userID`, `hasCompletedOnboarding`), and Copilot its own `COPILOT_HOME` inside it. On macOS the logins live in your keychain, which is found from `HOME`, so `home/Library/Keychains` links to yours: the agent can still query it with `security`, as before. On Vertex, gcloud reads your credentials from `CLOUDSDK_CONFIG` (your `~/.config/gcloud`), and the agent can reach them too.
- **Names that say nothing.** A sandbox has a random name; the run's name (case, variant) only names its logs. A stub finds the sandbox from its own path.
- **State the agent cannot touch.** What `before` records is kept by the harness and only reaches the checks, after the agent.
- **The `integrity` metric** (`integrity.py`): a call that mentions a path under your `HOME` or the repo, the folder that holds the sandboxes (another run's), a file of the sandbox that is the harness's (anything but the workspace, the plugin, `origin.git` and the agent's `HOME` and `TMPDIR`), or a name of the eval (`golden.yaml`, `marketplace_evals`, `test_evals`, `.runs/`, `EVAL_SANDBOX`, `stub-gaps.jsonl`, `gh.log`) fails the run. Paths of the system (`/usr`, `/opt/homebrew`...) and commands such as `env` or `ps` do not count.

None of this stops an agent set on it: `ps` still shows the repo's path, and a path built at run time (`$(which gh)`) is not seen. What it does is not hand the eval to the agent, keep the checks out of its reach, and turn any attempt that shows in its calls into a failed run. Isolating the agent for real takes a container per run, with its own credentials proxy.

### With `--provider claude`

`claude -p "<prompt>" --plugin-dir <plugin> --model <id>`, with `--setting-sources project`, `--strict-mcp-config` and `--no-session-persistence`, so no user settings, plugins, hooks or MCPs reach it. The golden's tools become `--allowedTools` (`read` → Read, `edit` → Edit + Write, `search` → Grep + Glob, `execute` → Bash, plus Skill) with `--permission-mode dontAsk`: anything else is denied without asking and stays in the trace as denied. Checked on Claude Code 2.1.289.

### With `--provider copilot`

Checked on Copilot CLI 1.0.91:

- `copilot -p "<prompt>" --plugin-dir <plugin> --model <id> --output-format json`. Each run gets an empty `COPILOT_HOME` that only carries your `copilot login` (the `lastLoggedInUser`/`loggedInUsers` entries of `~/.copilot/config.json`; the token stays in the keychain), and no `COPILOT_*` variables except the login ones, so nothing else from your own configuration reaches it, plus `--disable-builtin-mcps` and `--no-ask-user`.
- Permissions come from the golden's `tools`: `read`/`search` → `read`, `edit` → `write`, `execute` → `shell`, passed as `--allow-tool`. Anything else is denied without asking and stays in the trace as denied. Copilot auto-approves read-only shell commands.
- The trace comes from the JSONL events (`tool.execution_start`/`complete`, `assistant.message`). `apply_patch` becomes one `edit_file` per updated file and one `write_file` per new file. Tokens come from an OpenTelemetry file export of the same session (the JSONL has none), and AI credits from its last `session.usage_checkpoint`.
- Copilot exits with code 0 even when it cannot start (for example, a model the account cannot use): a run without a `result` event fails with the CLI's error.
- Copilot runs `gh auth token` itself at startup. A fixture's `gh` stub must not log it as the agent's work: see `fixtures/gh` of `git-workflow`.
- Not verified: delegation. No skill evaluated so far delegates, so every call is attributed to `main`.
- The judge runs with no tools (`--available-tools ask_user --no-ask-user`) and no custom instructions, with the prompt on stdin. Copilot has no `--json-schema`, so the schema goes in the prompt and the answer must parse as JSON.

### With `--provider copilot --backend vertex`

The same runner and judge, with the models of Vertex AI instead of GitHub Copilot's, through Copilot's BYOK provider:

- Copilot has no Google provider type, so it uses `COPILOT_PROVIDER_TYPE=openai` against Vertex's OpenAI-compatible endpoint. `openai` is only the wire format: the requests go to Vertex, in your project, and are billed there.
- The project and location come from `EVALS_VERTEX_PROJECT` and `EVALS_VERTEX_LOCATION`, so nothing about GCP is in the repo. Some models are only served in the `global` location; the regional endpoints answer 404.
- Authentication is your Application Default Credentials: Copilot runs `gcloud auth application-default print-access-token` before every request, so a long session never outlives its token. gcloud finds them through `CLOUDSDK_CONFIG`, since the agent's `HOME` is not yours. In CI, Workload Identity Federation provides the same credentials.
- No GitHub login: the session gets neither your `copilot login` nor `COPILOT_GITHUB_TOKEN`, `GH_TOKEN` or `GITHUB_TOKEN`, so no model can come from GitHub.
- Vertex reports reasoning tokens apart from output tokens, so they get their own column. There are no AI credits, and the usage table leaves out the cost and credit columns when nothing reports them.

Each golden case runs N times, only once, and each metric is a pytest test that scores those same runs. A metric passes the case if at least `--min-passes` of the `--runs` runs pass it, or all of them if it is strict (a prohibition).

## Baseline: what the skill adds

If the agent already does a case right without the skill, the case does not measure the skill. `--baseline` runs each case `--runs` more times without it, at the same time as the runs with it, and compares both:

- Only the skill under evaluation is left out: its folder is removed from the plugin's copy in the sandbox, and the session still loads the plugin with `--plugin-dir`. The plugin's other skills, MCP servers and hooks stay loaded, as a user without the skill would have them. (`claude plugin eval --ablation` leaves out the whole plugin instead.)
- What only the skill can pass does not count in either group: a call matcher that loads the skill by its name, or reads a file of its folder (`@plugins/<plugin>/skills/<skill>/...`), in `expected_calls` or `forbidden_calls`. An `any_of` with one such alternative counts as one. Nothing in the golden marks them. A metric left with nothing to score (`expected_calls` in `git-workflow`, which only loads the skill) has no baseline: `–`.
- For each metric, the difference (Δ) is the pass rate with the skill minus without it, in points: 3/3 with it and 1/3 without it is +67 pp. The call metrics score both groups without what only the skill can pass, so their "with" rate can differ from the metric's own result. The other metrics reuse their result with the skill: the judge is not asked twice.
- For each case, whether it passes with and without the skill, with the same rules (`--min-passes`, strict metrics N of N) on the metrics both can be scored on. A case that passes without the skill is flagged `⚠ passes without the skill: it does not measure it`.
- It is informative: whether a test passes or fails is decided by the runs with the skill alone. Each metric's report adds the line `baseline: with skill 3/3 · without skill 1/3 · Δ +67 pp` (and, with several prompt variants, `baseline by variant: directo 1/1 vs 0/1 (+100 pp) · …`) and the runs without the skill; the `eval baseline` section at the end lists each case. The runs without the skill leave their logs in `.runs/<date>/` as `<case>-baseline-<i>-...`.
- It doubles the agent's cost and the judge's, except for the `skill_loading` prompts, which do not run without the skill.

## Efficiency: what the runs spend

Next to whether a case passes, what its runs spend: a skill that does the same with twice the tokens, or adds ten turns to every commit, does not show in the metrics. Nothing new is collected: every run's trace already has it. It never passes or fails.

- Per run: turns (model replies) and tool calls, adding up the main agent and its subagents; total tokens; cost, in dollars with Claude and AI credits with Copilot (`–` when the runtime does not report it, never 0); and the run's time.
- Per case: the median of its runs without errors (another model, a runtime that failed), which do not count for the metrics either; their spending is still in the session's usage. The median is not moved by one run that went astray; that run is in `per_run`. Prompt variants are not broken down, and the `skill_loading` prompts get it too.
- With `--baseline`: the median without the skill, and per measure the difference with the skill minus without it, absolute and in percent of the value without it (`+3 (+50 %)`; no percent over 0).
- It shows in the `eval efficiency` table at the end (terminal and `summary.txt`), in `results.json` (`efficiency` in each case: `with_skill` and `without_skill` with their valid `runs`, `total_runs`, `median` and `per_run`, and `delta`) and in `evals-compare`. The PR comment only has the session's usage. The `eval usage` table and `results.json` keep the agent's runs without the skill in their own row (`agent without skill`).

## Requirements

| What | Version | Notes |
|---|---|---|
| **uv** | 0.12.x | `pyproject.toml` requires `uv_build>=0.12.19,<0.13` |
| **Python** | 3.14 | Pinned in `.python-version`. If it is missing, `uv sync` downloads it |
| **git** and **bash** | | The fixtures' setup scripts and the `inspect` commands use them |
| **jq** | | The `gh` stub's `--jq` and the `git-workflow` checks |

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
uv run pytest -m eval -s --baseline -k git-workflow    # also without the skill: what it adds
uv run pytest -m eval -s -k "git-workflow and should-"   # only whether the skill loads when it should
```

### Adding a case or a skill

1. Add the fixture in `evals/plugins/<plugin>/skills/<skill>/fixtures/<fixture>/`, with a `setup.sh` if it needs one.
2. Add the case to that skill's `golden.yaml`, or create the golden. No test file is needed: `tests/evals/test_evals.py` runs every golden it finds.
3. `uv run pytest` checks that the golden is in place and points at things that exist.
4. `uv run pytest -m eval -s -k <plugin>/<skill>/<case-id>`.
5. If the agent cannot pass it reliably yet, mark it `informative: true`, and remove the mark once it does.

## CI

Two workflows in `../.github/workflows/`:

- **`evals-unit-tests.yml`** (`evals unit tests` check): `ruff` and `uv run pytest` on every PR, not only those touching `evals/`, since it also checks the goldens against the plugins. It is meant to be a required check, and a required check whose workflow never starts stays pending and blocks the merge. It takes under a minute and calls no LLM.
- **`evals.yml`** (`evals` check): the real evals, `--provider copilot --backend vertex --runs 3 --min-passes 2`, when a PR changes `plugins/**`, `evals/**` or the workflow itself. It is informative: a case below the minimum, or a prohibition broken in any run, turns it red, but it is not required. The quality metrics of an `informative` case never turn it red. A new push cancels the run of the previous one. PRs from forks skip it, since they cannot sign in to Google Cloud. It leaves:
  - a PR comment with each case's metrics and the usage (`uv run evals-report`), updated on every push;
  - the same report in the job summary, plus `summary.txt` with the judge's reasons;
  - the `evals-runs` artifact: `.runs/`, with the log, final files and state of every run.

  With the `evals:baseline` label on the PR, it also runs each case without its skill (`--baseline`), and the report gets a column for each case without the skill and, per metric, the runs without it and the difference. Adding the label starts a run; other labels do not. It can also be run by hand (`workflow_dispatch`) on any branch, with or without `baseline`: then it posts no PR comment, only the job summary and the artifact.

It signs in to Google Cloud without keys, through Workload Identity Federation, as a service account that can only call Vertex AI (`roles/aiplatform.user`). The workflow reads everything about GCP from repo variables, so none of it is in this public repo: `GCP_WIF_PROVIDER`, `GCP_EVALS_SERVICE_ACCOUNT`, `EVALS_VERTEX_PROJECT` and `EVALS_VERTEX_LOCATION`. Copilot CLI is pinned in the workflow; change it there and in the requirements above together.
