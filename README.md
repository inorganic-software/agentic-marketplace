# agentic-marketplace

Plugin marketplace for Claude Code and GitHub Copilot.

## Plugins

| Plugin | What it does |
| --- | --- |
| `hello-world` | Sample plugin. Its `hello-world` skill writes `Hola mundo` to `hello-world.txt`, and its `bye-world` skill writes `Bye world` to `bye-world.txt`, both in the current folder. |
| `commons` | Skills shared across repos and plugins. Its `git-workflow` skill covers trunk-based development, Conventional Branch names, Conventional Commits, pull requests and resolving rebase conflicts. |

Skills have behavioral evals in `evals/`, which run them on Claude Code and Copilot CLI and judge what the agent does: see [evals/README.md](evals/README.md).

## Use it in a repo

Add this to the repo's `.claude/settings.json`, which Claude Code, Copilot CLI and Copilot in VS Code read:

```json
{
  "extraKnownMarketplaces": {
    "agentic-marketplace": {
      "source": { "source": "github", "repo": "inorganic-software/agentic-marketplace" }
    }
  },
  "enabledPlugins": {
    "hello-world@agentic-marketplace": true
  }
}
```

- For Copilot cloud agent, add the same two keys to `.github/copilot/settings.json`.
- Copilot CLI installs the plugin on its own, only for that repo. VS Code shows it as a recommended plugin.
- Claude Code enables it but does not download it: each person runs this once in the repo:

```bash
claude plugin install hello-world@agentic-marketplace --scope project
```

## Use it for yourself

Claude Code:

```bash
claude plugin marketplace add inorganic-software/agentic-marketplace
claude plugin install hello-world@agentic-marketplace
```

Copilot CLI:

```bash
copilot plugin marketplace add inorganic-software/agentic-marketplace
copilot plugin install hello-world@agentic-marketplace
```

## Run the skill

- Claude Code: `/hello-world:hello-world` or `/hello-world:bye-world`
- Copilot: ask it to use the `hello-world` or `bye-world` skill.
