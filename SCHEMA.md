# nano_brain schema design

## Overview

A personal second brain that learns from Claude Code sessions. Captures rules, tracks history, and provides observability across projects.

---

## Tables

### rule_sets

Groups of rules that can be assigned to projects.

```sql
rule_sets (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,      -- "python-strict", "default"
    description TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
)
```

### rules

Enforceable constraints. Regex patterns or semantic matches.

```sql
rules (
    id INTEGER PRIMARY KEY,
    rule_set_id INTEGER,            -- NULL = global (applies everywhere)
    type TEXT DEFAULT 'regex',      -- 'regex' or 'semantic'
    pattern TEXT,                   -- regex pattern (for type='regex')
    description TEXT NOT NULL,      -- what this rule does / semantic match
    tool TEXT,                      -- Bash, Edit, Write, or NULL (all)
    action TEXT DEFAULT 'block',    -- block, warn, log
    active INTEGER DEFAULT 1,
    priority INTEGER DEFAULT 0,
    problem TEXT,                   -- context: what problem this solves
    solution TEXT,                  -- context: the solution/approach
    source_message_id INTEGER,      -- which message created this rule
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (rule_set_id) REFERENCES rule_sets(id),
    FOREIGN KEY (source_message_id) REFERENCES messages(id)
)
```

### rule_embeddings

Vector storage for semantic rule matching (existing).

```sql
CREATE VIRTUAL TABLE rule_embeddings USING vec0(
    rule_id INTEGER PRIMARY KEY,
    embedding FLOAT[384]
)
```

### projects

Codebases/folders where Claude Code runs.

```sql
projects (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,      -- ~/apps/nano_brain
    name TEXT,                      -- display name, defaults to folder name
    rule_set_id INTEGER,            -- which rules apply here
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (rule_set_id) REFERENCES rule_sets(id)
)
```

### sessions

A Claude Code conversation/work session.

```sql
sessions (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL,
    task TEXT,                      -- "Add FastAPI CRUD viewer"
    started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    ended_at DATETIME,
    status TEXT DEFAULT 'active',   -- active, completed, abandoned
    FOREIGN KEY (project_id) REFERENCES projects(id)
)
```

### messages

Individual messages in a session (user or assistant).

```sql
messages (
    id INTEGER PRIMARY KEY,
    session_id INTEGER NOT NULL,
    role TEXT NOT NULL,             -- 'user' or 'assistant'
    content TEXT,                   -- message text
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
)
```

### tool_calls

Tool invocations within assistant messages.

```sql
tool_calls (
    id INTEGER PRIMARY KEY,
    message_id INTEGER NOT NULL,
    tool TEXT NOT NULL,             -- Bash, Edit, Write, Read, etc.
    input TEXT,                     -- tool input/arguments (JSON)
    output TEXT,                    -- tool result
    success INTEGER DEFAULT 1,      -- 1=success, 0=failed
    duration_ms INTEGER,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (message_id) REFERENCES messages(id)
)
```

### rule_triggers

When a rule matched a tool call.

```sql
rule_triggers (
    id INTEGER PRIMARY KEY,
    rule_id INTEGER NOT NULL,
    tool_call_id INTEGER NOT NULL,
    action_taken TEXT NOT NULL,     -- block, warn, log
    llm_reasoning TEXT,             -- for semantic rules, why it matched
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (rule_id) REFERENCES rules(id),
    FOREIGN KEY (tool_call_id) REFERENCES tool_calls(id)
)
```

---

## Relationships

```
rule_sets
    └── rules (many)
    └── projects (many)

projects
    └── sessions (many)

sessions
    └── messages (many)

messages
    └── tool_calls (many)
    └── rules (as source_message_id)

tool_calls
    └── rule_triggers (many)

rules
    └── rule_triggers (many)
```

---

## Key queries

**Get all rules for a project:**
```sql
SELECT r.* FROM rules r
LEFT JOIN rule_sets rs ON r.rule_set_id = rs.id
LEFT JOIN projects p ON p.rule_set_id = rs.id
WHERE p.id = ? OR r.rule_set_id IS NULL
```

**Session history with tool calls:**
```sql
SELECT m.*, tc.tool, tc.input, tc.success
FROM messages m
LEFT JOIN tool_calls tc ON tc.message_id = m.id
WHERE m.session_id = ?
ORDER BY m.timestamp
```

**Rules triggered in a session:**
```sql
SELECT r.description, rt.action_taken, tc.tool, tc.input
FROM rule_triggers rt
JOIN rules r ON rt.rule_id = r.id
JOIN tool_calls tc ON rt.tool_call_id = tc.id
JOIN messages m ON tc.message_id = m.id
WHERE m.session_id = ?
```

**Find which message created a rule:**
```sql
SELECT r.*, m.content, s.task
FROM rules r
JOIN messages m ON r.source_message_id = m.id
JOIN sessions s ON m.session_id = s.id
WHERE r.id = ?
```

---

## Migration plan

1. Create `rule_sets` table, add "default" set
2. Add `rule_set_id` to `rules` (nullable)
3. Create `projects` table
4. Create `sessions` table
5. Create `messages` table
6. Create `tool_calls` table
7. Create `rule_triggers` table
8. Add `source_message_id` to `rules`

---

## Notes

- `rule_set_id = NULL` means global rule (applies to all projects)
- Projects without a rule_set get global rules only
- History is append-only, never delete sessions/messages
- Tool call outputs may be truncated for storage
