# Scoped Rules Design

## Overview

Rules match regex patterns against tool input. When matched, either take action immediately (dumb) or flag for LLM review (smart).

## Rule Fields

```
┌────────────────────────────────────────────────────┐
│                      RULE                          │
├────────────────────────────────────────────────────┤
│  pattern      │ Single regex (legacy)              │
│  patterns     │ JSON array of regex patterns       │
│  llm_review   │ 0 = direct action, 1 = LLM decides │
│  prompt       │ Context for LLM review             │
│  action       │ block / warn / log                 │
│  tool         │ Bash / Edit / Write / NULL (all)   │
└────────────────────────────────────────────────────┘
```

## Flow

```
Tool Input: {"file_path": "main.py", "content": "..."}
                         │
                         ▼
              ┌─────────────────────┐
              │  Pattern matches?   │  (pattern OR patterns array)
              └──────────┬──────────┘
                         │
           ┌─────────────┴─────────────┐
           │ no                        │ yes
           ▼                           ▼
        ALLOW                 ┌─────────────────┐
                              │  llm_review?    │
                              └────────┬────────┘
                                       │
                         ┌─────────────┴─────────────┐
                         │ false                     │ true
                         ▼                           ▼
                   DIRECT ACTION              LLM REVIEWS
                   (block/warn)               with prompt
                                                   │
                                          ┌───────┴───────┐
                                          ▼               ▼
                                      APPROVE         REJECT
```

## Examples

### Dumb Rule: Block Dangerous Commands
```json
{
  "type": "regex",
  "patterns": "[\"rm -rf\", \"DROP TABLE\", \"format c:\"]",
  "llm_review": false,
  "action": "block"
}
```
Pattern matches → immediate block. Fast, no LLM.

### Smart Rule: Review Auth File Changes
```json
{
  "type": "regex",
  "patterns": "[\"auth/.*\\.py\", \"login\", \"password\"]",
  "llm_review": true,
  "prompt": "Does this change weaken security or bypass authentication?",
  "action": "block"
}
```
Pattern matches → LLM reviews content with prompt → decides.

### Smart Rule: Protect main.py
```json
{
  "type": "regex",
  "patterns": "[\"main\\.py\"]",
  "llm_review": true,
  "prompt": "Is this change safe for the application entry point?",
  "action": "warn"
}
```
Any write to main.py → LLM reviews → warns if suspicious.

## Hooks

### Pre-hook (check_rules.py)
Runs before tool execution:
1. Check regex patterns against tool input
2. If match + `llm_review=false` → direct action
3. If match + `llm_review=true` → LLM evaluates with `prompt`

### Stop-hook (learning_agent.py)
Runs after session ends:
1. Extract learnings from conversation
2. Create rules with patterns, llm_review, prompt

## Database Schema

```sql
-- New columns in rules table
patterns TEXT,        -- JSON array: ["pattern1", "pattern2"]
llm_review INTEGER,   -- 0 = direct action, 1 = LLM decides
prompt TEXT           -- Context for LLM review
```

## Summary

- **patterns**: JSON array of regex to match tool input
- **llm_review**: dumb (false) = instant, smart (true) = LLM reviews
- **prompt**: what to tell the LLM when reviewing
- **action**: block/warn/log when triggered
