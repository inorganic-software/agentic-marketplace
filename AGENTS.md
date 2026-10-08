# Agentic marketplace

This repo is a plugin marketplace for Claude Code and GitHub Copilot. The catalog is `.claude-plugin/marketplace.json`, and each plugin lives in its own folder under `plugins/`.

## This repo is public

- Anyone can read it, including people outside the organization. Do not add secrets, tokens, internal URLs, client names or client code, in plugins or in commits.

## Plugin format

- Every plugin uses the **Claude Code plugin format**. Claude Code only reads this format, and Copilot CLI and Copilot in VS Code read it too, so one plugin serves all of them.
- Do not use the Agent Plugins 1.0 format (a `plugin.json` with `$schema` at the plugin root, or a `com.github.copilot/` folder): Claude Code does not read it.
- A plugin has this layout. Only `plugin.json` goes inside `.claude-plugin/`; every component folder sits at the plugin root:

```
plugins/<plugin>/
├── .claude-plugin/plugin.json   manifest: name, description, version, author
├── skills/<skill>/SKILL.md      skills
├── agents/<agent>.md            agents
├── commands/<command>.md        slash commands
├── hooks/hooks.json             hooks
└── .mcp.json                    MCP servers
```

- Reference files inside the plugin with `${CLAUDE_PLUGIN_ROOT}`, for example in hook commands and MCP server configs. Copilot expands it too.
- Prefer skills and MCP servers: they behave the same in Claude Code and Copilot. Agents and hooks differ between clients, so test them in both before publishing them.

## Adding or changing a plugin

- Plugin and skill names are kebab-case. A plugin's `name` is how repos enable it (`<plugin>@agentic-marketplace`), so never rename a published plugin: add a new one instead.
- Register every plugin in `.claude-plugin/marketplace.json` with `name`, `description` and `source` (`./plugins/<plugin>`).
- The version lives only in the plugin's `plugin.json`, never in its marketplace entry. Bump it, following semver, in every change to the plugin: clients only update a plugin when its version changes.
- Validate before committing, and fix every error and warning:

```bash
claude plugin validate --strict .
claude plugin validate --strict plugins/<plugin>
```

- List each plugin in the table in `README.md`, with one line on what it does.

## Evals

- Skills are evaluated in `evals/` (see `evals/README.md`). The evals mirror the plugins, as tests mirror the code in Java: the golden of `plugins/<plugin>/skills/<skill>/` is `evals/plugins/<plugin>/skills/<skill>/golden.yaml`, with its fixtures in `fixtures/` next to it.
- When you add or change a skill, add or update its golden in the same change. Write each case's prompt as a user would, without naming the skill.
- Fixtures must not reach the network: a remote is a local bare repository and an external CLI is a stub in `$EVAL_SANDBOX/bin`.
- Run the linter and the unit tests before committing; the tests also check that every golden is in place and points at things that exist:

```bash
cd evals && uv run ruff check && uv run ruff format --check && uv run pytest
```

- Run the real evals of what you changed (slow, and they use model quota): `cd evals && uv run pytest -m eval -s -k <plugin>/<skill>`.
