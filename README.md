# Causeway

Rule enforcement and learning for Claude Code.

## Install

```bash
git clone https://github.com/codimusmaximus/causeway.git && cd causeway && uv sync
```

## Setup

```bash
cd your-project
uv run causeway connect
```

Restart Claude Code to activate.

## Commands

```bash
uv run causeway connect     # Add hooks + MCP to current project
uv run causeway list        # List active rules
uv run causeway add <set>   # Add ruleset (python-safety, git-safety, secrets)
uv run causeway ui          # Start dashboard
```

## How It Works

- **Pre-hook**: Checks rules before every tool call (block/warn)
- **Stop-hook**: Learns from session, creates rules automatically

## License

© 2025 Prismeta. All rights reserved. See [TERMS.md](TERMS.md).
