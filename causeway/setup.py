#!/usr/bin/env python3
"""Setup causeway hooks and MCP for a Claude Code project."""
import json
import os
import sys
from pathlib import Path

CAUSEWAY_DIR = Path(__file__).parent.resolve()


def get_claude_settings_path(project_path: Path) -> Path:
    """Get the Claude settings file path for a project."""
    return project_path / ".claude" / "settings.json"


def get_mcp_settings_path() -> Path:
    """Get the global MCP settings path."""
    return Path.home() / ".claude" / "mcp_servers.json"


def setup_hooks(project_path: Path):
    """Add causeway hooks to project's Claude settings."""
    settings_path = get_claude_settings_path(project_path)
    settings_path.parent.mkdir(parents=True, exist_ok=True)

    settings = {}
    if settings_path.exists():
        settings = json.loads(settings_path.read_text())

    # Pre-tool hook for rule checking
    hooks = settings.setdefault("hooks", {})
    hooks["PreToolUse"] = [
        {
            "matcher": "Bash|Write|Edit",
            "hooks": [f"python3 {CAUSEWAY_DIR}/scripts/check_rules.py"]
        }
    ]

    settings_path.write_text(json.dumps(settings, indent=2))
    print(f"Updated hooks in {settings_path}")


def setup_mcp():
    """Add causeway MCP server to global config."""
    mcp_path = get_mcp_settings_path()
    mcp_path.parent.mkdir(parents=True, exist_ok=True)

    servers = {}
    if mcp_path.exists():
        servers = json.loads(mcp_path.read_text())

    servers["causeway"] = {
        "command": "python3",
        "args": [str(CAUSEWAY_DIR / "brain_mcp.py")],
        "env": {
            "CAUSEWAY_DB": str(CAUSEWAY_DIR / "brain.db")
        }
    }

    mcp_path.write_text(json.dumps(servers, indent=2))
    print(f"Added causeway MCP server to {mcp_path}")


def print_usage():
    print("""
causeway setup

Usage:
    python setup.py hooks <project-path>   Add pre-tool hooks to a project
    python setup.py mcp                    Add MCP server globally
    python setup.py all <project-path>     Setup both hooks and MCP

Examples:
    python setup.py hooks /path/to/my-project
    python setup.py mcp
    python setup.py all .
""")


def main():
    if len(sys.argv) < 2:
        print_usage()
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "hooks":
        if len(sys.argv) < 3:
            print("Error: project path required")
            sys.exit(1)
        setup_hooks(Path(sys.argv[2]).resolve())

    elif cmd == "mcp":
        setup_mcp()

    elif cmd == "all":
        if len(sys.argv) < 3:
            print("Error: project path required")
            sys.exit(1)
        setup_hooks(Path(sys.argv[2]).resolve())
        setup_mcp()

    else:
        print_usage()
        sys.exit(1)


if __name__ == "__main__":
    main()
