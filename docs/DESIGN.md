# Causeway - Local Architecture

## Overview
Self-learning guardrails for Claude Code. Runs locally, learns from corrections, enforces rules.

## Components

```
~/.causeway/
├── brain.db          # SQLite - rules, traces, history
├── .env              # API keys (OpenAI for learning agent)
├── .install_id       # Unique install identifier
├── scripts/
│   ├── check_rules.py   # PreToolUse hook - evaluates rules
│   └── ping.sh          # SessionStart hook - version check
├── learning_agent.py    # Stop hook - extracts rules from session
├── brain_mcp.py         # MCP server - Claude queries rules
├── server.py            # Dashboard UI (localhost:8000)
└── causeway_cli.py      # CLI entrypoint
```

## Database Schema (SQLite)

### rules
Stores enforcement rules (learned + predefined).
- `id`, `type` (regex/semantic), `pattern`, `description`
- `action` (block/warn), `tool` (Bash/Edit/Write)
- `solution`, `active`, `created_at`

### traces
Logs rule evaluations for debugging.
- `id`, `rule_id`, `tool`, `input`, `result`, `created_at`

### history
Session transcripts for learning.
- `id`, `session_id`, `messages`, `created_at`

## Hooks

| Hook | Script | Purpose |
|------|--------|---------|
| PreToolUse | check_rules.py | Block/warn before tool execution |
| Stop | learning_agent.py | Extract rules from corrections |
| SessionStart | ping.sh | Version check + telemetry |

## Data Flow

```
1. User starts Claude Code session
   └─> SessionStart hook → ping.sh → API (telemetry)

2. Claude attempts tool use (Bash/Edit/Write)
   └─> PreToolUse hook → check_rules.py
       ├─> Regex rules: fast pattern match
       └─> Semantic rules: LLM evaluation
       └─> Returns: allow / block / warn

3. User corrects Claude ("No, use ALTER TABLE not DROP")
   └─> Correction stored in session

4. Session ends
   └─> Stop hook → learning_agent.py
       └─> Analyzes transcript
       └─> Extracts new rules
       └─> Saves to brain.db
```

## Privacy
- All rules stored locally
- No rule content sent to cloud
- Only telemetry: install_id, version, platform (anonymous)
