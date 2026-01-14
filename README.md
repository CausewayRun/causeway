# Causeway

A self-learning hooks system for Claude Code.

Causeway watches your Claude Code sessions and automatically learns your preferences. When you correct Claude or express a preference, Causeway captures it as a rule and enforces it in future sessions.

![Causeway Dashboard](screen.png)

## How It Works

1. **You work with Claude** - code, fix bugs, make changes
2. **Causeway observes** - watches for corrections and preferences
3. **Rules are created** - "use uv not pip", "don't modify config.py"
4. **Future sessions enforce** - Claude is warned or blocked before repeating mistakes

The feedback loop means Claude gets smarter about your codebase over time.

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