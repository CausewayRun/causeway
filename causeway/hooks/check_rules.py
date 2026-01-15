#!/usr/bin/env python3
"""Pre-flight hook: Check rules using semantic AI agent."""
import sys
import os
import json
import asyncio
import time
import re
from dotenv import load_dotenv

# Load .env from project root
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(os.path.join(project_root, '.env'))

from causeway.rule_agent import check_with_agent, sync_all_rule_embeddings
from causeway.db import init_db, get_connection


def log_trace(tool_name: str, tool_input: str, rules_checked: int, rules_matched: int,
              matched_rule_ids: list, decision: str, reason: str, duration_ms: int,
              llm_prompt: str = None, llm_response: str = None):
    """Log a trace of the hook execution."""
    try:
        conn = get_connection()
        conn.execute("""
            INSERT INTO traces (hook_type, tool_name, tool_input, rules_checked, rules_matched,
                               matched_rule_ids, decision, reason, llm_prompt, llm_response, duration_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, ('pre', tool_name, tool_input[:1000], rules_checked, rules_matched,
              json.dumps(matched_rule_ids), decision, reason,
              llm_prompt[:2000] if llm_prompt else None,
              llm_response[:2000] if llm_response else None,
              duration_ms))
        conn.commit()
        conn.close()
    except Exception:
        pass  # Don't fail the hook if logging fails


def extract_rule_ids(comment: str) -> list[int]:
    """Extract rule IDs from comment like '[BLOCK #5] ...'"""
    return [int(m) for m in re.findall(r'#(\d+)', comment or '')]


async def check_rules_async(tool_name: str, tool_input: str, justification: str = None) -> tuple[bool, str, str]:
    """
    Check if tool input violates any rules using the AI agent.
    Returns (allowed, action, comment) - action is "block", "warn", or "allow".
    """
    init_db()

    # Ensure all rules have embeddings
    sync_all_rule_embeddings()

    # Run the agent with justification
    decision = await check_with_agent(tool_name, tool_input, justification)
    return decision.approved, decision.action, decision.comment


def main():
    start_time = time.time()

    # Read hook input from stdin (JSON format from Claude Code)
    hook_input_raw = sys.stdin.read()

    try:
        hook_input = json.loads(hook_input_raw) if hook_input_raw else {}
    except json.JSONDecodeError:
        hook_input = {}

    # Extract tool name and input from the hook data
    tool_name = hook_input.get('tool_name', 'unknown')
    tool_input = hook_input.get('tool_input', {})

    # Extract description/justification if present (Claude's reason for the action)
    justification = None
    if isinstance(tool_input, dict):
        justification = tool_input.get('description') or tool_input.get('justification')

    # Convert tool input to string for analysis
    if isinstance(tool_input, str):
        tool_input_str = tool_input
    else:
        tool_input_str = json.dumps(tool_input, indent=2)

    try:
        # Run async check with justification
        allowed, action, comment = asyncio.run(check_rules_async(tool_name, tool_input_str, justification))
    except Exception as e:
        duration_ms = int((time.time() - start_time) * 1000)
        log_trace(tool_name, tool_input_str, 0, 0, [], 'error', str(e), duration_ms)
        print(f"BLOCKED: Rule check error: {e}", file=sys.stderr)
        sys.exit(2)

    duration_ms = int((time.time() - start_time) * 1000)
    matched_ids = extract_rule_ids(comment)

    # Count rules (rough estimate from DB)
    try:
        conn = get_connection()
        rules_checked = conn.execute("SELECT COUNT(*) FROM rules WHERE active = 1").fetchone()[0]
        conn.close()
    except Exception:
        rules_checked = 0

    log_trace(tool_name, tool_input_str, rules_checked, len(matched_ids), matched_ids,
              action, comment, duration_ms)

    # If the agent approved, allow regardless of action type
    if allowed:
        sys.exit(0)

    if action == "block":
        # Exit code 2 = block, stderr shown to Claude
        # Hard rules cannot be overridden
        print(f"BLOCKED: {comment}", file=sys.stderr)
        print("This is a hard rule and cannot be overridden.", file=sys.stderr)
        sys.exit(2)

    if action == "warn":
        # Exit code 2 = also block, but with suggestion instead of hard rejection
        # Soft rules can be overridden with justification
        print(f"SUGGESTION: {comment}", file=sys.stderr)
        print("To override: start your description with 'OVERRIDE:' followed by justification.", file=sys.stderr)
        sys.exit(2)

    # Exit 0 to allow the action
    sys.exit(0)


if __name__ == "__main__":
    main()
