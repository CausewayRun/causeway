"""Learning agent that extracts rules from conversations."""
import os
import sys
import json
import asyncio
from pathlib import Path
from dotenv import load_dotenv
from pydantic import BaseModel
from pydantic_ai import Agent

# Load .env from the project root
load_dotenv(Path(__file__).parent.parent / ".env")

from db import init_db
from brain_mcp import call_tool
from history_logger import log_transcript


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

LEARNING_PROMPT = """You are a learning agent. Extract reusable knowledge from conversations as rules.

TWO RULE TYPES:

1. **regex**: Pattern-matched (fast). Use for security/dangerous patterns.
   - pattern="rm -rf" → blocks destructive commands
   - patterns='["^pip ", "^pip3 "]' → multiple patterns

2. **semantic**: Embedding-matched (uses LLM). Use for preferences/guidelines.

RULE STRUCTURE:
- description: Short summary (e.g., "Use uv for package management")
- problem: What went wrong / what to avoid
- solution: How to fix / what to do instead
- tool: Which tool this applies to (Bash, Edit, Write) or omit for all
- rule_action: "block" for security issues, "warn" for style preferences
- patterns: JSON array of regex patterns to match (e.g., '["main\\.py", "auth/.*"]')
- llm_review: If true, LLM reviews matched content before taking action
- prompt: Context for LLM review (e.g., "Check if this weakens security")

EXAMPLES:

**Dumb rule** (immediate block):
  type="regex", patterns='["rm -rf", "DROP TABLE"]', rule_action="block"

**Smart rule** (LLM reviews):
  type="regex", patterns='["main\\.py", "config\\.py"]', llm_review=true
  prompt="Check if this change breaks the entry point or config"

**Preference** (semantic, LLM matches):
  type="semantic", description="Use uv not pip", rule_action="warn"

WHAT TO CAPTURE:

**User preferences** → semantic rule, warn
**Corrections** → semantic rule, warn
**Dangerous patterns** → regex rule, block
**File-specific rules** → regex with patterns + llm_review=true

ACTIONS:
- CREATE: New preference, correction, or solution
- UPDATE: Refine existing rule (use rule_id)
- DELETE: User says stop (use rule_id)

Return empty list only if nothing reusable."""


def get_learning_agent() -> Agent:
    global _learning_agent
    if _learning_agent is None:
        _learning_agent = Agent(
            'openai:gpt-5',
            output_type=LearningOutput,
            system_prompt=LEARNING_PROMPT,
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
    prompt: str | None = None
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


def format_transcript(transcript: list) -> str:
    """Format transcript into readable text."""
    lines = []
    for entry in transcript:
        entry_type = entry.get("type", "")
        if entry_type not in ("user", "assistant"):
            continue

        msg = entry.get("message", {})
        role = msg.get("role", entry_type)
        content = msg.get("content", "")

        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict):
                    if item.get("type") == "text":
                        parts.append(item.get("text", ""))
                    elif item.get("type") == "tool_use":
                        parts.append(f"[Tool: {item.get('name')}]")
                    elif item.get("type") == "tool_result":
                        result = item.get("content", "")
                        if len(str(result)) > 200:
                            result = str(result)[:200] + "..."
                        parts.append(f"[Result: {result}]")
            text = " ".join(parts)
        else:
            text = str(content)

        if text.strip():
            lines.append(f"{role.upper()}: {text[:1000]}")

    return "\n\n".join(lines)


async def extract_rule_changes(transcript: list) -> LearningOutput:
    """Extract rule changes from conversation."""
    init_db()

    existing_text = await get_existing_rules()

    conversation = format_transcript(transcript)

    prompt = f"""Analyze this conversation and extract rule changes.

EXISTING RULES:
{existing_text}

CONVERSATION:
{conversation}

What rules should be created, updated, or deleted?"""

    agent = get_learning_agent()
    result = await agent.run(prompt)
    return result.output


async def process_transcript(transcript: list, log_fn=None) -> str:
    """Process transcript and apply rule changes."""
    def log(msg):
        if log_fn:
            log_fn(msg)

    formatted = format_transcript(transcript)
    log(f"Formatted conversation:\n{formatted[:500]}")

    log("Extracting rule changes...")
    output = await extract_rule_changes(transcript)

    if not output.changes:
        log("No rule changes needed")
        return "No rule changes"

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
                    prompt=change.prompt
                )
                results.append(result)
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
                log(f"Updated: {result}")

            elif action == "delete" and change.rule_id:
                result = await delete_rule(change.rule_id)
                results.append(result)
                log(f"Deleted: {result}")

        except Exception as e:
            log(f"Error applying change: {e}")
            results.append(f"Error: {e}")

    return f"Applied {len(results)} changes:\n" + "\n".join(results)


def main():
    """Entry point for Stop hook."""
    import asyncio

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

    transcript_path = os.path.expanduser(transcript_path)

    if not os.path.exists(transcript_path):
        log(f"Path does not exist: {transcript_path}")
        sys.exit(0)

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
        sys.exit(0)

    if not transcript:
        log("Empty transcript")
        sys.exit(0)

    # Log session history to database
    try:
        history_stats = log_transcript(transcript_path, log)
        log(f"History logged: {history_stats}")
    except Exception as e:
        log(f"Error logging history: {e}")

    # Extract learnings from transcript
    try:
        result = asyncio.run(process_transcript(transcript, log))
        print(f"[Learning Agent] {result}", file=sys.stderr)
    except Exception as e:
        log(f"Error processing transcript: {e}")
        print(f"[Learning Agent Error] {e}", file=sys.stderr)

    log("Done")
    sys.exit(0)


if __name__ == "__main__":
    main()
