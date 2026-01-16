"""Learning agent that extracts rules from conversations."""
import os
import sys
import json
import asyncio
import time
from pathlib import Path
from dotenv import load_dotenv
from pydantic import BaseModel
from pydantic_ai import Agent

# Load .env from causeway directory
load_dotenv(Path(__file__).parent / ".env")

from .db import init_db, get_connection
from .mcp import call_tool
from .history_logger import log_transcript


def log_trace(transcript_path: str, rules_created: int, rules_updated: int, rules_deleted: int,
              llm_prompt: str, llm_response: str, duration_ms: int):
    """Log a trace of the learning agent execution."""
    try:
        conn = get_connection()
        conn.execute("""
            INSERT INTO traces (hook_type, tool_name, tool_input, rules_checked, rules_matched,
                               matched_rule_ids, decision, reason, llm_prompt, llm_response, duration_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, ('stop', 'learning_agent', transcript_path[:500],
              rules_created + rules_updated + rules_deleted, rules_created,
              json.dumps({'created': rules_created, 'updated': rules_updated, 'deleted': rules_deleted}),
              'learn', f"Created {rules_created}, updated {rules_updated}, deleted {rules_deleted}",
              llm_prompt[:2000] if llm_prompt else None,
              llm_response[:2000] if llm_response else None,
              duration_ms))
        conn.commit()
        conn.close()
    except Exception:
        pass


class RuleChange(BaseModel):
    """A single rule change."""
    action: str  # "create", "update", "delete"
    rule_id: int | None = None  # For update/delete
    type: str | None = None  # "regex" or "semantic" (for create)
    pattern: str | None = None  # Single regex pattern (legacy)
    patterns: str | None = None  # JSON array of regex patterns
    description: str | None = None  # Short summary of the rule
    problem: str | None = None  # What went wrong / what to avoid
    solution: str | None = None  # How it was resolved / what to do instead
    tool: str | None = None  # Bash, Edit, Write, or None for all
    rule_action: str | None = None  # block, warn, log
    reason: str  # Why this change is being made
    llm_review: bool | None = None  # If true, LLM reviews matched content
    prompt: str | None = None  # Context for LLM review


class LearningOutput(BaseModel):
    """Output from the learning agent."""
    changes: list[RuleChange]
    summary: str


_learning_agent = None

LEARNING_PROMPT = """You are a learning agent. Extract rules ONLY from concrete evidence in conversations.

CRITICAL: Do NOT invent or hallucinate rules. Only create rules when you see:
1. An ACTUAL mistake/error that was corrected (quote the problem and fix)
2. User EXPLICITLY requests a rule (e.g., "always use X", "never do Y")

If neither condition is met, return an empty list.

RULE TYPES:
- regex: Fast pattern match. Use for dangerous commands.
- semantic: LLM-matched. Use for preferences.

REQUIRED EVIDENCE:
- problem: Quote the ACTUAL error/mistake from conversation
- solution: Quote the ACTUAL fix/correction applied

EXAMPLES OF VALID RULES:
- User tried "pip install X", got told to use "uv add X" → Create rule
- User said "always use typescript" → Create rule
- Code failed, was fixed with specific pattern → Create rule

EXAMPLES OF INVALID RULES (DO NOT CREATE):
- No error occurred but you think something "could be better"
- Generic best practices not discussed in conversation
- Assumptions about what user might want

ACTIONS:
- CREATE: Only with concrete evidence (quote problem + solution)
- UPDATE: Refine existing rule based on new evidence
- DELETE: User explicitly says to remove a rule

Default to empty list. Only create rules with clear justification."""


def get_setting(key: str, default: str) -> str:
    """Get a setting from DB or return default."""
    try:
        conn = get_connection()
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        conn.close()
        return row['value'] if row else default
    except Exception:
        return default


def get_learning_agent() -> Agent:
    global _learning_agent
    # Always recreate to pick up settings changes
    model = get_setting('learn_model', 'openai:gpt-5')
    prompt = get_setting('learn_prompt', LEARNING_PROMPT)
    _learning_agent = Agent(
        model,
        output_type=LearningOutput,
        system_prompt=prompt,
    )
    return _learning_agent


async def get_existing_rules() -> str:
    """Get all active rules via MCP."""
    result = await call_tool('list_rules', {'active_only': True})
    return result[0].text


async def create_rule(
    rule_type: str,
    description: str,
    pattern: str | None = None,
    patterns: str | None = None,
    problem: str | None = None,
    solution: str | None = None,
    tool: str | None = None,
    action: str = "block",
    llm_review: bool | None = None,
    prompt: str | None = None,
    source_session_id: int | None = None
) -> str:
    """Create a new rule via MCP."""
    args = {'type': rule_type, 'description': description, 'action': action}
    if pattern:
        args['pattern'] = pattern
    if patterns:
        args['patterns'] = patterns
    if problem:
        args['problem'] = problem
    if solution:
        args['solution'] = solution
    if tool:
        args['tool'] = tool
    if llm_review is not None:
        args['llm_review'] = llm_review
    if prompt:
        args['prompt'] = prompt
    if source_session_id is not None:
        args['source_session_id'] = source_session_id
    result = await call_tool('add_rule', args)
    return result[0].text


async def update_rule(
    rule_id: int,
    pattern: str | None = None,
    patterns: str | None = None,
    description: str | None = None,
    problem: str | None = None,
    solution: str | None = None,
    action: str | None = None,
    llm_review: bool | None = None,
    prompt: str | None = None
) -> str:
    """Update an existing rule via MCP."""
    args = {'id': rule_id}
    if pattern is not None:
        args['pattern'] = pattern
    if patterns is not None:
        args['patterns'] = patterns
    if description is not None:
        args['description'] = description
    if problem is not None:
        args['problem'] = problem
    if solution is not None:
        args['solution'] = solution
    if action is not None:
        args['action'] = action
    if llm_review is not None:
        args['llm_review'] = llm_review
    if prompt is not None:
        args['prompt'] = prompt
    result = await call_tool('update_rule', args)
    return result[0].text


async def delete_rule(rule_id: int) -> str:
    """Delete a rule via MCP."""
    result = await call_tool('delete_rule', {'id': rule_id})
    return result[0].text


def format_transcript(transcript: list, max_entries: int = 30, max_chars: int = 8000) -> str:
    """Format transcript into readable text, limited for context efficiency."""
    # Only process recent entries
    recent = transcript[-max_entries:] if len(transcript) > max_entries else transcript

    lines = []
    total_chars = 0

    for entry in recent:
        entry_type = entry.get("type", "")
        if entry_type not in ("user", "assistant"):
            continue

        msg = entry.get("message", {})
        role = msg.get("role", entry_type)
        content = msg.get("content", "")

        if isinstance(content, str):
            text = content[:500]  # Limit individual messages
        elif isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict):
                    if item.get("type") == "text":
                        parts.append(item.get("text", "")[:300])
                    elif item.get("type") == "tool_use":
                        parts.append(f"[Tool: {item.get('name')}]")
                    elif item.get("type") == "tool_result":
                        # Skip tool results - too verbose
                        pass
            text = " ".join(parts)
        else:
            text = str(content)[:300]

        if text.strip():
            line = f"{role.upper()}: {text[:400]}"
            if total_chars + len(line) > max_chars:
                break
            lines.append(line)
            total_chars += len(line)

    return "\n\n".join(lines)


async def extract_rule_changes(transcript: list) -> tuple[LearningOutput, str, str]:
    """Extract rule changes from conversation. Returns (output, prompt, response)."""
    init_db()

    existing_text = await get_existing_rules()
    # Limit existing rules context
    if len(existing_text) > 3000:
        existing_text = existing_text[:3000] + "\n... (truncated)"

    conversation = format_transcript(transcript)

    prompt = f"""Analyze this conversation and extract rule changes.

EXISTING RULES:
{existing_text}

CONVERSATION:
{conversation}

What rules should be created, updated, or deleted?"""

    agent = get_learning_agent()
    result = await agent.run(prompt)

    # Format response for logging
    response = json.dumps({
        'changes': [c.model_dump() for c in result.output.changes],
        'summary': result.output.summary
    }, indent=2)

    return result.output, prompt, response


async def process_transcript(transcript: list, log_fn=None, session_id: int = None) -> tuple[str, str, str, dict]:
    """Process transcript and apply rule changes. Returns (result, prompt, response, stats)."""
    def log(msg):
        if log_fn:
            log_fn(msg)

    formatted = format_transcript(transcript)
    log(f"Formatted conversation:\n{formatted[:500]}")

    log("Extracting rule changes...")
    output, llm_prompt, llm_response = await extract_rule_changes(transcript)

    stats = {'created': 0, 'updated': 0, 'deleted': 0}

    if not output.changes:
        log("No rule changes needed")
        return "No rule changes", llm_prompt, llm_response, stats

    log(f"Found {len(output.changes)} rule change(s)")

    results = []
    for change in output.changes:
        try:
            action = change.action.lower()
            if action == "create":
                result = await create_rule(
                    rule_type=change.type or "semantic",
                    description=change.description or "",
                    pattern=change.pattern,
                    patterns=change.patterns,
                    problem=change.problem,
                    solution=change.solution,
                    tool=change.tool,
                    action=change.rule_action or "warn",
                    llm_review=change.llm_review,
                    prompt=change.prompt,
                    source_session_id=session_id
                )
                results.append(result)
                stats['created'] += 1
                log(f"Created: {result}")

            elif action == "update" and change.rule_id:
                result = await update_rule(
                    rule_id=change.rule_id,
                    pattern=change.pattern,
                    patterns=change.patterns,
                    description=change.description,
                    problem=change.problem,
                    solution=change.solution,
                    action=change.rule_action,
                    llm_review=change.llm_review,
                    prompt=change.prompt
                )
                results.append(result)
                stats['updated'] += 1
                log(f"Updated: {result}")

            elif action == "delete" and change.rule_id:
                result = await delete_rule(change.rule_id)
                results.append(result)
                stats['deleted'] += 1
                log(f"Deleted: {result}")

        except Exception as e:
            log(f"Error applying change: {e}")
            results.append(f"Error: {e}")

    return f"Applied {len(results)} changes:\n" + "\n".join(results), llm_prompt, llm_response, stats


def run_learning(transcript_path: str):
    """Run the actual learning process (called in background)."""
    debug_log = Path(__file__).parent / "hook_debug.log"

    def log(msg):
        with open(debug_log, "a") as f:
            f.write(f"{msg}\n")

    log(f"[{__import__('datetime').datetime.now()}] Learning started for {transcript_path}")

    transcript_path = os.path.expanduser(transcript_path)

    if not os.path.exists(transcript_path):
        log(f"Path does not exist: {transcript_path}")
        return

    transcript = []
    try:
        with open(transcript_path, 'r') as f:
            for line in f:
                line = line.strip()
                if line:
                    transcript.append(json.loads(line))
        log(f"Loaded {len(transcript)} transcript entries")
    except Exception as e:
        log(f"Error reading transcript: {e}")
        return

    if not transcript:
        log("Empty transcript")
        return

    # Log session history to database
    session_id = None
    try:
        history_stats = log_transcript(transcript_path, log)
        session_id = history_stats.get('session_id')
        log(f"History logged: {history_stats}")
    except Exception as e:
        log(f"Error logging history: {e}")

    # Extract learnings from transcript
    start_time = time.time()
    try:
        result, llm_prompt, llm_response, stats = asyncio.run(process_transcript(transcript, log, session_id=session_id))
        duration_ms = int((time.time() - start_time) * 1000)

        # Log trace
        log_trace(
            transcript_path=transcript_path,
            rules_created=stats['created'],
            rules_updated=stats['updated'],
            rules_deleted=stats['deleted'],
            llm_prompt=llm_prompt,
            llm_response=llm_response,
            duration_ms=duration_ms
        )

        log(f"Learning complete: {result}")
    except Exception as e:
        duration_ms = int((time.time() - start_time) * 1000)
        log_trace(transcript_path, 0, 0, 0, None, str(e), duration_ms)
        log(f"Error processing transcript: {e}")

    log("Done")


def main():
    """Entry point for Stop hook - forks to background immediately."""
    import subprocess

    # Handle --learn argument (background mode)
    if len(sys.argv) >= 3 and sys.argv[1] == "--learn":
        run_learning(sys.argv[2])
        return

    debug_log = Path(__file__).parent / "hook_debug.log"

    def log(msg):
        with open(debug_log, "a") as f:
            f.write(f"{msg}\n")

    log(f"[{__import__('datetime').datetime.now()}] Stop hook triggered")

    hook_input_raw = sys.stdin.read()
    log(f"Raw input length: {len(hook_input_raw) if hook_input_raw else 0}")

    try:
        hook_input = json.loads(hook_input_raw) if hook_input_raw else {}
    except json.JSONDecodeError as e:
        log(f"JSON decode error: {e}")
        sys.exit(0)

    transcript_path = hook_input.get("transcript_path")
    log(f"transcript_path: {transcript_path}")

    if not transcript_path:
        log("No transcript_path, exiting")
        sys.exit(0)

    # Fork to background and exit immediately
    log("Forking to background...")
    causeway_root = Path(__file__).parent.parent  # Go up from causeway/ to project root
    subprocess.Popen(
        ["uv", "run", "--directory", str(causeway_root), "causeway-learn", "--learn", transcript_path],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True
    )
    log("Forked, exiting hook")
    sys.exit(0)


if __name__ == "__main__":
    main()
