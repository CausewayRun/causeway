#!/usr/bin/env python3
"""
run_scenario.py - Execute a single E2E test scenario

Loads a YAML scenario file, runs Claude Code with the specified prompt,
and verifies the result by checking the traces table in the database.

Exit codes:
  0 - Test passed
  1 - Test failed
  2 - Test skipped (optional scenario)
"""

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

# Try to import yaml, fall back to simple parser if not available
try:
    import yaml
except ImportError:
    yaml = None

import re

# Patterns that might contain API keys or secrets
SENSITIVE_PATTERNS = [
    (r'(ANTHROPIC_API_KEY|OPENAI_API_KEY|API_KEY|SECRET|TOKEN|PASSWORD)([=:]\s*)[^\s\'"]+', r'\1\2[REDACTED]'),
    (r'(sk-[a-zA-Z0-9]{20,})', '[REDACTED_KEY]'),
    (r'(sk-ant-[a-zA-Z0-9-]{20,})', '[REDACTED_KEY]'),
]


def filter_sensitive_output(text: str) -> str:
    """Remove potential API keys and secrets from output text."""
    result = text
    for pattern, replacement in SENSITIVE_PATTERNS:
        result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)
    return result


def parse_yaml_simple(content: str) -> dict:
    """Simple YAML parser for basic key-value scenarios."""
    result = {}
    current_key = None
    current_value = []
    indent_level = 0

    for line in content.split('\n'):
        stripped = line.strip()

        # Skip empty lines and comments
        if not stripped or stripped.startswith('#'):
            continue

        # Check for key: value or key:
        if ':' in stripped and not stripped.startswith('-'):
            # If we were building a multi-line value, save it
            if current_key and current_value:
                result[current_key] = '\n'.join(current_value).strip()
                current_value = []

            key, _, value = stripped.partition(':')
            key = key.strip()
            value = value.strip()

            # Handle quoted strings
            if value.startswith('"') and value.endswith('"'):
                value = value[1:-1]
            elif value.startswith("'") and value.endswith("'"):
                value = value[1:-1]

            # Handle booleans
            if value.lower() == 'true':
                value = True
            elif value.lower() == 'false':
                value = False
            # Handle integers
            elif value.isdigit():
                value = int(value)

            if value:
                result[key] = value
                current_key = None
            else:
                current_key = key
                # Check if next content is a nested dict
                result[key] = {}
        elif current_key:
            # Handle nested keys or list items
            if stripped.startswith('-'):
                # List item
                if current_key not in result or not isinstance(result[current_key], list):
                    result[current_key] = []
                item = stripped[1:].strip()
                # Handle "- command: value" format
                if ':' in item:
                    k, _, v = item.partition(':')
                    result[current_key].append({k.strip(): v.strip().strip('"').strip("'")})
                else:
                    result[current_key].append(item)
            elif ':' in stripped:
                # Nested key
                if not isinstance(result[current_key], dict):
                    result[current_key] = {}
                k, _, v = stripped.partition(':')
                v = v.strip().strip('"').strip("'")
                if v.lower() == 'true':
                    v = True
                elif v.lower() == 'false':
                    v = False
                result[current_key][k.strip()] = v

    return result


def load_scenario(scenario_path: str) -> dict:
    """Load a scenario from a YAML file."""
    with open(scenario_path, 'r') as f:
        content = f.read()

    if yaml:
        return yaml.safe_load(content)
    else:
        return parse_yaml_simple(content)


def run_setup_commands(setup_commands: list, project_dir: str) -> bool:
    """Run setup commands before the test."""
    if not setup_commands:
        return True

    for cmd_item in setup_commands:
        if isinstance(cmd_item, dict):
            command = cmd_item.get('command', '')
        else:
            command = str(cmd_item)

        if not command:
            continue

        print(f"  Setup: {command}")
        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=project_dir,
                capture_output=True,
                text=True,
                timeout=30
            )
            if result.returncode != 0:
                print(f"  Setup command failed: {result.stderr}")
                return False
        except subprocess.TimeoutExpired:
            print(f"  Setup command timed out")
            return False

    return True


def run_claude_code(prompt: str, project_dir: str, timeout: int = 120) -> tuple[int, str, str]:
    """
    Run Claude Code CLI with the given prompt.

    Returns (exit_code, stdout, stderr)
    """
    # Build the command
    cmd = [
        'claude',
        '--print',
        '--dangerously-skip-permissions',
        '--max-turns', '1',
        '-p', prompt
    ]

    print(f"  Running: claude --print --dangerously-skip-permissions --max-turns 1 -p \"{prompt[:50]}...\"")

    try:
        result = subprocess.run(
            cmd,
            cwd=project_dir,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, 'CAUSEWAY_CWD': project_dir}
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return -1, '', f'Claude Code timed out after {timeout}s'
    except Exception as e:
        return -1, '', str(e)


def get_latest_trace(db_path: str) -> dict | None:
    """Get the most recent trace from the database."""
    if not os.path.exists(db_path):
        print(f"  Database not found: {db_path}")
        return None

    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.execute("""
            SELECT hook_type, tool_name, tool_input, decision, reason, matched_rule_ids
            FROM traces
            WHERE hook_type = 'pre'
            ORDER BY timestamp DESC
            LIMIT 1
        """)
        row = cursor.fetchone()
        conn.close()

        if row:
            return dict(row)
        return None
    except Exception as e:
        print(f"  Database error: {e}")
        return None


def verify_trace(trace: dict, verify_config: dict) -> tuple[bool, str]:
    """
    Verify that the trace matches the expected outcome.

    Returns (passed, message)
    """
    if not trace:
        return False, "No trace found in database"

    expected_decision = verify_config.get('decision', '').lower()
    expected_tool = verify_config.get('tool_name', '')
    reason_contains = verify_config.get('reason_contains', '')

    actual_decision = (trace.get('decision') or '').lower()
    actual_tool = trace.get('tool_name') or ''
    actual_reason = trace.get('reason') or ''

    # Check decision
    if expected_decision and actual_decision != expected_decision:
        return False, f"Expected decision '{expected_decision}', got '{actual_decision}'"

    # Check tool name
    if expected_tool and actual_tool != expected_tool:
        return False, f"Expected tool '{expected_tool}', got '{actual_tool}'"

    # Check reason contains
    if reason_contains and reason_contains.lower() not in actual_reason.lower():
        return False, f"Expected reason to contain '{reason_contains}', got '{actual_reason}'"

    return True, f"Decision: {actual_decision}, Tool: {actual_tool}"


def main():
    parser = argparse.ArgumentParser(description='Run a single E2E test scenario')
    parser.add_argument('scenario', help='Path to the scenario YAML file')
    parser.add_argument('--project-dir', default=os.environ.get('CAUSEWAY_CWD', '/test-project'),
                        help='Project directory to run tests in')
    args = parser.parse_args()

    # Load scenario
    print(f"Loading scenario: {args.scenario}")
    try:
        scenario = load_scenario(args.scenario)
    except Exception as e:
        print(f"Failed to load scenario: {e}")
        sys.exit(1)

    name = scenario.get('name', os.path.basename(args.scenario))
    description = scenario.get('description', '')
    prompt = scenario.get('prompt', '')
    verify_config = scenario.get('verify', {})
    setup_commands = scenario.get('setup', [])
    timeout = scenario.get('timeout', 120)
    optional = scenario.get('optional', False)

    print(f"Scenario: {name}")
    if description:
        print(f"Description: {description}")

    if not prompt:
        print("ERROR: No prompt specified in scenario")
        sys.exit(1)

    # Run setup commands
    if setup_commands:
        print("\nRunning setup commands...")
        if not run_setup_commands(setup_commands, args.project_dir):
            print("Setup failed!")
            sys.exit(2 if optional else 1)

    # Run Claude Code
    print("\nRunning Claude Code...")
    exit_code, stdout, stderr = run_claude_code(prompt, args.project_dir, timeout)

    print(f"\nClaude Code exit code: {exit_code}")
    if stdout:
        # Filter potential API keys from output
        safe_stdout = filter_sensitive_output(stdout[:500])
        print(f"Stdout (first 500 chars): {safe_stdout}")
    if stderr:
        safe_stderr = filter_sensitive_output(stderr[:500])
        print(f"Stderr (first 500 chars): {safe_stderr}")

    # Give a moment for DB writes to complete
    time.sleep(0.5)

    # Verify the trace
    print("\nVerifying trace in database...")
    db_path = os.path.join(args.project_dir, '.causeway', 'brain.db')
    trace = get_latest_trace(db_path)

    if trace:
        print(f"  Found trace: decision={trace.get('decision')}, tool={trace.get('tool_name')}")
        print(f"  Matched rules: {trace.get('matched_rule_ids')}")
    else:
        print("  No trace found!")

    # Verify
    if verify_config:
        passed, message = verify_trace(trace, verify_config)
        print(f"\nVerification: {message}")

        if passed:
            print(f"\n{'='*40}")
            print(f"PASSED: {name}")
            print(f"{'='*40}")
            sys.exit(0)
        else:
            print(f"\n{'='*40}")
            print(f"FAILED: {name}")
            print(f"{'='*40}")
            sys.exit(2 if optional else 1)
    else:
        # No verification - just check that Claude ran
        print("\nNo verification config - checking Claude Code ran successfully")
        if exit_code == 0 or trace:
            print(f"\n{'='*40}")
            print(f"PASSED: {name}")
            print(f"{'='*40}")
            sys.exit(0)
        else:
            print(f"\n{'='*40}")
            print(f"FAILED: {name}")
            print(f"{'='*40}")
            sys.exit(2 if optional else 1)


if __name__ == '__main__':
    main()
