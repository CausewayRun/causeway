#!/usr/bin/env python3
"""Pre-flight hook: Check rules using semantic AI agent."""
import sys
import os
import json
import asyncio
from dotenv import load_dotenv

# Load .env from project root
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(os.path.join(project_root, '.env'))

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rule_agent import check_with_agent, sync_all_rule_embeddings
from db import init_db


async def check_rules_async(tool_name: str, tool_input: str) -> tuple[bool, str, str]:
    """
    Check if tool input violates any rules using the AI agent.
    Returns (allowed, action, comment) - action is "block", "warn", or "allow".
    """
    init_db()

    # Ensure all rules have embeddings
    sync_all_rule_embeddings()

    # Run the agent
    decision = await check_with_agent(tool_name, tool_input)
    return decision.approved, decision.action, decision.comment


def main():
    # Read hook input from stdin (JSON format from Claude Code)
    hook_input_raw = sys.stdin.read()

    try:
        hook_input = json.loads(hook_input_raw) if hook_input_raw else {}
    except json.JSONDecodeError:
        hook_input = {}

    # Extract tool name and input from the hook data
    tool_name = hook_input.get('tool_name', 'unknown')
    tool_input = hook_input.get('tool_input', {})

    # Convert tool input to string for analysis
    if isinstance(tool_input, str):
        tool_input_str = tool_input
    else:
        tool_input_str = json.dumps(tool_input, indent=2)

    try:
        # Run async check
        allowed, action, comment = asyncio.run(check_rules_async(tool_name, tool_input_str))
    except Exception as e:
        # On any error, block with error message
        print(f"BLOCKED: Rule check error: {e}", file=sys.stderr)
        sys.exit(2)

    if action == "block":
        # Exit code 2 = block, stderr shown to Claude
        print(f"BLOCKED: {comment}", file=sys.stderr)
        sys.exit(2)

    if action == "warn":
        # Exit code 2 = also block, but with suggestion instead of hard rejection
        print(f"SUGGESTION: {comment}", file=sys.stderr)
        sys.exit(2)

    # Exit 0 to allow the action
    sys.exit(0)


if __name__ == "__main__":
    main()
