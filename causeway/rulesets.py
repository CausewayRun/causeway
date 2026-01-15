"""Predefined rulesets for common use cases."""

RULESETS = {
    "python-safety": {
        "description": "Python best practices and safety",
        "rules": [
            {"type": "regex", "pattern": r"^pip3? install", "description": "Use uv instead of pip", "action": "warn", "tool": "Bash", "solution": "uv add"},
            {"type": "regex", "pattern": r"^python [^3]", "description": "Use python3 explicitly", "action": "warn", "tool": "Bash", "solution": "python3"},
            {"type": "regex", "pattern": r"rm -rf /", "description": "Dangerous rm command", "action": "block", "tool": "Bash"},
        ],
    },
    "git-safety": {
        "description": "Prevent dangerous git operations",
        "rules": [
            {"type": "regex", "pattern": r"git push.*(--force|-f)", "description": "No force push", "action": "block", "tool": "Bash"},
            {"type": "regex", "pattern": r"git reset --hard", "description": "Dangerous reset", "action": "warn", "tool": "Bash"},
        ],
    },
    "secrets": {
        "description": "Block hardcoded secrets",
        "rules": [
            {"type": "regex", "pattern": r"(api[_-]?key|secret|password|token)\s*[=:]\s*['\"][^'\"]+['\"]", "description": "Hardcoded secret detected", "action": "block"},
        ],
    },
}
