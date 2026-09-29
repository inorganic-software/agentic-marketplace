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
