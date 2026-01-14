# Causeway

Rule enforcement and learning for Claude Code.

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/codimusmaximus/causeway/main/install.sh | bash
```

## Setup

```bash
cd your-project
causeway connect
```

Restart Claude Code to activate.

## Commands

```bash
causeway connect     # Add hooks + MCP to current project
causeway list        # List active rules
causeway add <set>   # Add ruleset (python-safety, git-safety, secrets)
causeway ui          # Start dashboard
```

## How It Works

- **Pre-hook**: Checks rules before every tool call (block/warn)
- **Stop-hook**: Learns from session, creates rules automatically

## License

© 2025 Prismeta. All rights reserved. See [TERMS.md](TERMS.md).
