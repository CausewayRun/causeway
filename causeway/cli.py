#!/usr/bin/env python3
"""Causeway CLI - rule enforcement for Claude Code."""
import json
import os
import re
import subprocess
import sys
import urllib.request
import urllib.error
from pathlib import Path

API_URL = "https://causeway-api.fly.dev"
EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")

CAUSEWAY_DIR = Path(__file__).parent.resolve()  # Package dir: ~/.causeway/causeway
CAUSEWAY_ROOT = CAUSEWAY_DIR.parent  # Project root: ~/.causeway (where pyproject.toml is)
ORIG_CWD = os.environ.get("CAUSEWAY_CWD") or os.getcwd()

# Re-exec with uv if dependencies missing
try:
    import sqlite_vec
except ImportError:
    env = os.environ.copy()
    env["CAUSEWAY_CWD"] = ORIG_CWD
    os.chdir(CAUSEWAY_DIR)
    os.execvpe("uv", ["uv", "run", "python3", __file__] + sys.argv[1:], env)

def cmd_init():
    """Initialize causeway in current directory."""
    from rich.console import Console
    console = Console()

    project_path = Path(ORIG_CWD)
    causeway_dir = project_path / ".causeway"
    db_path = causeway_dir / "brain.db"

    # Init database in project's .causeway/ folder
    sys.path.insert(0, str(CAUSEWAY_DIR))
    from db import init_db
    init_db(db_path)

    console.print(f"[green]✓[/green] Initialized database at [dim]{db_path}[/dim]")

def load_config():
    """Load existing config from .env file."""
    env_file = CAUSEWAY_DIR / ".env"
    config = {}
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                key, val = line.split("=", 1)
                config[key.strip()] = val.strip()
    return config


def save_config(config: dict):
    """Save config to .env file."""
    env_file = CAUSEWAY_DIR / ".env"
    lines = [f"{k}={v}" for k, v in config.items()]
    env_file.write_text("\n".join(lines) + "\n")


def get_install_id():
    """Get or create install ID."""
    id_file = CAUSEWAY_DIR / ".install_id"
    if id_file.exists():
        return id_file.read_text().strip()
    import uuid
    install_id = str(uuid.uuid4())
    id_file.write_text(install_id)
    return install_id


def register_user(email: str, provider: str):
    """Register user with API (subscribes to updates)."""
    try:
        data = {
            "install_id": get_install_id(),
            "email": email,
            "source": "cli",
        }
        req = urllib.request.Request(
            f"{API_URL}/subscribe",
            data=json.dumps(data).encode(),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass  # Silent fail - don't block setup


def validate_api_key(provider: str, api_key: str) -> tuple[bool, str]:
    """Validate API key by making a test request."""
    try:
        if provider == "openai":
            req = urllib.request.Request(
                "https://api.openai.com/v1/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            urllib.request.urlopen(req, timeout=10)
            return True, "Valid"
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return False, "Invalid API key"
        elif e.code == 403:
            return False, "Access denied - check permissions"
        return False, f"Error: {e.code}"
    except Exception as e:
        return False, f"Connection error: {str(e)[:30]}"
    return False, "Unknown error"


def interactive_setup():
    """Interactive setup wizard for first-time users."""
    from rich.console import Console
    from rich.panel import Panel
    from rich.prompt import Prompt
    from rich.text import Text

    console = Console()
    config = load_config()

    # Check if already configured (only API key is required)
    if config.get("OPENAI_API_KEY"):
        console.print("[dim]Already configured. Skipping setup.[/dim]\n")
        return config

    # Welcome banner
    console.print()
    console.print(Panel.fit(
        "[bold cyan]Welcome to Causeway[/bold cyan]\n[dim]Rule enforcement for Claude Code[/dim]",
        border_style="cyan",
        padding=(1, 4)
    ))
    console.print()

    # Email (optional)
    email = config.get("CAUSEWAY_EMAIL", "")
    if not email:
        console.print("[dim]We'd like your email to keep you up to date on The AI Substrate[/dim]")
        email = Prompt.ask("[bold]Email[/bold] [dim](optional, press Enter to skip)[/dim]", default="")
        if email:
            if EMAIL_REGEX.match(email):
                config["CAUSEWAY_EMAIL"] = email
            else:
                console.print("[yellow]Invalid email format - skipping[/yellow]")
                email = ""
        console.print()

    # Provider selection (OpenAI only)
    provider = config.get("CAUSEWAY_PROVIDER", "")
    if not provider:
        provider = "openai"
        config["CAUSEWAY_PROVIDER"] = provider

    # API Key
    key_name = "OPENAI_API_KEY"
    key_display = "OpenAI"

    if not config.get(key_name):
        from rich.status import Status
        import getpass
        while True:
            api_key = getpass.getpass(f"{key_display} API key: ")
            if not api_key:
                console.print("[red]API key is required.[/red]")
                continue

            # Show masked version
            masked = api_key[:4] + "*" * (len(api_key) - 8) + api_key[-4:] if len(api_key) > 8 else "*" * len(api_key)
            console.print(f"[dim]{masked}[/dim]")

            with Status("[dim]Validating...[/dim]", spinner="dots"):
                valid, msg = validate_api_key(provider, api_key)

            if valid:
                config[key_name] = api_key
                console.print(f"[green]\u2713[/green] Valid!")
                break
            else:
                console.print(f"[red]\u2717 {msg}[/red] - try again")
        console.print()

    # Call-home telemetry opt-in
    if "CAUSEWAY_CALL_HOME" not in config:
        from rich.prompt import Confirm
        console.print("[dim]Help improve Causeway by sending anonymous usage data[/dim]")
        console.print("[dim](install ID, version, platform - no personal data)[/dim]")
        call_home = Confirm.ask("Enable anonymous usage telemetry?", default=True)
        config["CAUSEWAY_CALL_HOME"] = "true" if call_home else "false"
        console.print()

    save_config(config)

    # Register with API (only if email provided)
    if email and provider:
        register_user(email, provider)

    console.print(Panel.fit(
        "[bold green]Configuration saved![/bold green]",
        border_style="green"
    ))
    console.print()

    return config


def cmd_connect():
    """Connect causeway to Claude Code."""
    ensure_set_up("connect")
    from rich.console import Console
    from rich.panel import Panel

    console = Console()
    project_path = Path(ORIG_CWD)

    # Interactive setup (global config - API keys, etc.)
    interactive_setup()

    # Create project-local .causeway/ folder and initialize database
    causeway_local = project_path / ".causeway"
    causeway_local.mkdir(parents=True, exist_ok=True)
    db_path = causeway_local / "brain.db"

    sys.path.insert(0, str(CAUSEWAY_DIR))
    from db import init_db
    init_db(db_path)
    console.print(f"[green]✓[/green] Initialized database at [dim]{db_path}[/dim]")

    # 1. Add hooks to current project
    settings_path = project_path / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)

    settings = {}
    if settings_path.exists():
        settings = json.loads(settings_path.read_text())

    hooks = settings.setdefault("hooks", {})

    # Pre-tool hook: check rules before execution
    # CAUSEWAY_CWD captures pwd before uv --directory changes it
    hooks["PreToolUse"] = [
        {
            "matcher": "*",
            "hooks": [{"type": "command", "command": f"CAUSEWAY_CWD=\"$(pwd)\" uv run --directory {CAUSEWAY_ROOT} causeway-check"}]
        }
    ]

    # Stop hook: learn from session after it ends
    hooks["Stop"] = [
        {
            "matcher": "*",
            "hooks": [{"type": "command", "command": f"CAUSEWAY_CWD=\"$(pwd)\" uv run --directory {CAUSEWAY_ROOT} causeway-learn"}]
        }
    ]

    # SessionStart hook: version check and telemetry (curl only, no deps)
    hooks["SessionStart"] = [
        {
            "matcher": "*",
            "hooks": [{"type": "command", "command": f"bash {CAUSEWAY_DIR}/hooks/ping.sh"}]
        }
    ]

    settings_path.write_text(json.dumps(settings, indent=2))
    console.print(f"[green]\u2713[/green] Added hooks to [dim]{settings_path}[/dim]")

    # 2. Add project-level .mcp.json with project-local database
    project_mcp = project_path / ".mcp.json"
    project_servers = {}
    if project_mcp.exists():
        project_servers = json.loads(project_mcp.read_text())

    mcp_servers = project_servers.setdefault("mcpServers", {})
    mcp_servers["causeway"] = {
        "type": "stdio",
        "command": "uv",
        "args": ["run", "--directory", str(CAUSEWAY_ROOT), "causeway-mcp"],
        "env": {"CAUSEWAY_DB": str(db_path)}
    }

    project_mcp.write_text(json.dumps(project_servers, indent=2))
    console.print(f"[green]\u2713[/green] Added MCP server to [dim]{project_mcp}[/dim]")

    console.print()
    console.print(Panel.fit(
        "[bold]Restart Claude Code to activate[/bold]",
        border_style="cyan"
    ))

def cmd_rulesets():
    """List available rulesets."""
    sys.path.insert(0, str(CAUSEWAY_DIR))
    from rulesets import RULESETS

    for name, data in RULESETS.items():
        print(f"{name:20} {data['description']} ({len(data['rules'])} rules)")

def cmd_add(ruleset: str):
    """Add a predefined ruleset."""
    ensure_set_up('add')
    sys.path.insert(0, str(CAUSEWAY_DIR))
    from rulesets import RULESETS
    from db import get_connection, init_db

    if ruleset not in RULESETS:
        print(f"Unknown ruleset: {ruleset}")
        print(f"Available: {', '.join(RULESETS.keys())}")
        sys.exit(1)

    init_db()
    conn = get_connection()
    rules = RULESETS[ruleset]["rules"]

    for rule in rules:
        conn.execute("""
            INSERT INTO rules (type, pattern, description, action, tool, solution, active)
            VALUES (?, ?, ?, ?, ?, ?, 1)
        """, (rule.get("type"), rule.get("pattern"), rule.get("description"),
              rule.get("action", "block"), rule.get("tool"), rule.get("solution")))
    conn.commit()
    conn.close()

    print(f"Added {len(rules)} rules from '{ruleset}'")

def cmd_list():
    """List active rules."""
    ensure_set_up('list')
    sys.path.insert(0, str(CAUSEWAY_DIR))
    from db import get_connection, init_db
    init_db()

    conn = get_connection()
    rows = conn.execute("SELECT id, type, action, description FROM rules WHERE active = 1 ORDER BY id").fetchall()
    conn.close()

    if not rows:
        print("No active rules")
        return

    for r in rows:
        print(f"#{r['id']:3} [{r['action']:5}] {r['description']}")

def cmd_ui():
    """Start the dashboard UI."""
    ensure_set_up('ui')
    env = os.environ.copy()
    env["CAUSEWAY_CWD"] = ORIG_CWD
    os.chdir(CAUSEWAY_DIR)
    os.execvpe("python3", ["python3", "server.py"], env)


def cmd_update(edge: bool = False):
    """Update causeway to latest version or edge."""
    from rich.console import Console
    from rich.panel import Panel

    console = Console()

    sys.path.insert(0, str(CAUSEWAY_DIR))
    from version import get_local_version, check_for_updates, clear_version_cache

    current = get_local_version()
    console.print(f"[dim]Current version:[/dim] {current}")

    if edge:
        # Update to edge (main branch)
        console.print("[dim]Updating to edge (main branch)...[/dim]")
        try:
            # Fetch and reset to origin/main
            result = subprocess.run(
                ["git", "fetch", "origin", "main"],
                cwd=CAUSEWAY_ROOT,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                console.print(f"[red]Failed to fetch:[/red] {result.stderr}")
                sys.exit(1)

            result = subprocess.run(
                ["git", "reset", "--hard", "origin/main"],
                cwd=CAUSEWAY_ROOT,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                console.print(f"[red]Failed to reset:[/red] {result.stderr}")
                sys.exit(1)

            # Reinstall dependencies
            _reinstall_deps(console)

            clear_version_cache()
            new_version = get_local_version()
            console.print()
            console.print(Panel.fit(
                f"[bold green]Updated to edge![/bold green]\n[dim]Now at:[/dim] {new_version}",
                border_style="green"
            ))
        except Exception as e:
            console.print(f"[red]Update failed:[/red] {e}")
            console.print("[dim]Try running 'git pull' manually in ~/.causeway[/dim]")
            sys.exit(1)
    else:
        # Check for updates
        update_info = check_for_updates()

        if update_info["on_edge"]:
            console.print("[yellow]You are on edge (ahead of latest release)[/yellow]")

        if not update_info["update_available"]:
            console.print("[green]Already up to date![/green]")
            return

        latest = update_info["latest_version"]
        console.print(f"[cyan]New version available:[/cyan] {latest}")

        try:
            # Fetch tags and checkout the new version
            result = subprocess.run(
                ["git", "fetch", "--tags"],
                cwd=CAUSEWAY_ROOT,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                console.print(f"[red]Failed to fetch tags:[/red] {result.stderr}")
                sys.exit(1)

            result = subprocess.run(
                ["git", "checkout", latest],
                cwd=CAUSEWAY_ROOT,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                console.print(f"[red]Failed to checkout {latest}:[/red] {result.stderr}")
                sys.exit(1)

            # Reinstall dependencies
            _reinstall_deps(console)

            clear_version_cache()
            new_version = get_local_version()
            console.print()
            console.print(Panel.fit(
                f"[bold green]Updated to {latest}![/bold green]\n[dim]Now at:[/dim] {new_version}",
                border_style="green"
            ))
        except Exception as e:
            console.print(f"[red]Update failed:[/red] {e}")
            console.print("[dim]Try running 'git pull' manually in ~/.causeway[/dim]")
            sys.exit(1)


def _reinstall_deps(console):
    """Reinstall dependencies using uv or pip."""
    # Detect if uv is available (preferred)
    uv_available = subprocess.run(
        ["which", "uv"],
        capture_output=True,
    ).returncode == 0

    console.print("[dim]Reinstalling dependencies...[/dim]")

    if uv_available:
        result = subprocess.run(
            ["uv", "sync"],
            cwd=CAUSEWAY_ROOT,
            capture_output=True,
            text=True,
        )
    else:
        # Fall back to pip
        result = subprocess.run(
            ["pip", "install", "-e", "."],
            cwd=CAUSEWAY_ROOT,
            capture_output=True,
            text=True,
        )

    if result.returncode != 0:
        console.print(f"[yellow]Warning: dependency install had issues:[/yellow] {result.stderr[:200]}")


def cmd_setup(reset: bool = False):
    """Run setup wizard. Use --reset to reconfigure everything."""
    from rich.console import Console
    from rich.panel import Panel
    from rich.prompt import Prompt, Confirm
    from rich.status import Status

    console = Console()
    config = load_config()

    if reset:
        # Clear config but keep install_id
        config = {}
        save_config(config)
        console.print("[yellow]Configuration reset.[/yellow]\n")

    # Show current config if exists
    if config.get("CAUSEWAY_EMAIL") or config.get("CAUSEWAY_PROVIDER") or config.get("OPENAI_API_KEY"):
        call_home_status = config.get("CAUSEWAY_CALL_HOME", "true")
        call_home_display = "[green]enabled[/green]" if call_home_status == "true" else "[yellow]disabled[/yellow]"
        console.print(Panel.fit(
            f"[bold]Current Configuration[/bold]\n\n"
            f"Email:     [cyan]{config.get('CAUSEWAY_EMAIL', '[not set]')}[/cyan]\n"
            f"Provider:  [cyan]{config.get('CAUSEWAY_PROVIDER', '[not set]')}[/cyan]\n"
            f"API Key:   [cyan]{'configured' if config.get('OPENAI_API_KEY') else '[not set]'}[/cyan]\n"
            f"Call-home: {call_home_display}",
            border_style="dim"
        ))
        console.print()

        if not Confirm.ask("Reconfigure?", default=False):
            return

        # Clear for reconfiguration
        config.pop("CAUSEWAY_EMAIL", None)
        config.pop("CAUSEWAY_PROVIDER", None)
        config.pop("OPENAI_API_KEY", None)

    # Run the setup wizard
    console.print()
    console.print(Panel.fit(
        "[bold cyan]Causeway Setup[/bold cyan]\n[dim]Rule enforcement for Claude Code[/dim]",
        border_style="cyan",
        padding=(1, 4)
    ))
    console.print()

    # Email (optional)
    console.print("[dim]We'd like your email to keep you up to date on The AI Substrate[/dim]")
    email = Prompt.ask("[bold]Email[/bold] [dim](optional, press Enter to skip)[/dim]", default="")
    if email:
        if EMAIL_REGEX.match(email):
            config["CAUSEWAY_EMAIL"] = email
        else:
            console.print("[yellow]Invalid email format - skipping[/yellow]")
            email = ""
    console.print()

    # Provider (OpenAI only)
    provider = "openai"
    config["CAUSEWAY_PROVIDER"] = provider

    # API Key
    key_name = "OPENAI_API_KEY"
    key_display = "OpenAI"

    import getpass
    while True:
        api_key = getpass.getpass(f"{key_display} API key: ")
        if not api_key:
            console.print("[red]API key is required.[/red]")
            continue

        # Show masked version
        masked = api_key[:4] + "*" * (len(api_key) - 8) + api_key[-4:] if len(api_key) > 8 else "*" * len(api_key)
        console.print(f"[dim]{masked}[/dim]")

        with Status("[dim]Validating...[/dim]", spinner="dots"):
            valid, msg = validate_api_key(provider, api_key)

        if valid:
            config[key_name] = api_key
            console.print(f"[green]\u2713[/green] Valid!")
            break
        else:
            console.print(f"[red]\u2717 {msg}[/red] - try again")
    console.print()

    # Call-home telemetry opt-in
    console.print("[dim]Help improve Causeway by sending anonymous usage data[/dim]")
    console.print("[dim](install ID, version, platform - no personal data)[/dim]")
    call_home = Confirm.ask("Enable anonymous usage telemetry?", default=True)
    config["CAUSEWAY_CALL_HOME"] = "true" if call_home else "false"
    console.print()

    save_config(config)

    # Register with API (only if email provided)
    if email:
        register_user(email, provider)

    console.print()
    console.print(Panel.fit(
        "[bold green]Setup complete![/bold green]\n\n"
        "Run [cyan]causeway connect[/cyan] in your project directory to enable rules.",
        border_style="green",
        padding=(1, 2)
    ))
    console.print()


def cmd_config(args: list[str]):
    """Show or modify configuration."""
    from rich.console import Console
    from rich.panel import Panel

    console = Console()
    config = load_config()

    # No args: show current config
    if not args:
        call_home_status = config.get("CAUSEWAY_CALL_HOME", "true")
        call_home_display = "[green]enabled[/green]" if call_home_status == "true" else "[yellow]disabled[/yellow]"
        console.print(Panel.fit(
            f"[bold]Current Configuration[/bold]\n\n"
            f"Email:     [cyan]{config.get('CAUSEWAY_EMAIL', '[not set]')}[/cyan]\n"
            f"Provider:  [cyan]{config.get('CAUSEWAY_PROVIDER', '[not set]')}[/cyan]\n"
            f"API Key:   [cyan]{'configured' if config.get('OPENAI_API_KEY') else '[not set]'}[/cyan]\n"
            f"Call-home: {call_home_display}",
            border_style="dim"
        ))
        return

    # Handle call-home subcommand
    if args[0] == "call-home":
        if len(args) == 1:
            # Show call-home status
            status = config.get("CAUSEWAY_CALL_HOME", "true")
            if status == "true":
                console.print("[green]Call-home telemetry is enabled[/green]")
            else:
                console.print("[yellow]Call-home telemetry is disabled[/yellow]")
            return

        action = args[1].lower()
        if action in ("on", "true", "enable", "enabled"):
            config["CAUSEWAY_CALL_HOME"] = "true"
            save_config(config)
            console.print("[green]Call-home telemetry enabled[/green]")
        elif action in ("off", "false", "disable", "disabled"):
            config["CAUSEWAY_CALL_HOME"] = "false"
            save_config(config)
            console.print("[yellow]Call-home telemetry disabled[/yellow]")
        else:
            console.print(f"[red]Unknown action: {action}[/red]")
            console.print("Usage: causeway config call-home [on|off]")
        return

    console.print(f"[red]Unknown config option: {args[0]}[/red]")
    console.print("Usage: causeway config [call-home [on|off]]")

def ensure_set_up(command: str = None):
    is_complete, missing = is_setup_complete()
    
    if not is_complete:
        from rich.console import Console
        from rich.panel import Panel
        
        console = Console()
        console.print()
        console.print(Panel.fit(
            f"[bold red]Setup Required[/bold red]\n\n"
            f"Missing configuration:\n" +
            "\n".join(f"  • {item}" for item in missing) +
            f"\n\n[dim]Run:[/dim] [cyan]causeway setup[/cyan]",
            border_style="red",
            padding=(1, 2)
        ))
        console.print()
        sys.exit(1)

def is_setup_complete() -> tuple[bool, list[str]]:
    config = load_config()
    missing = []
    
    if not config.get('OPENAI_API_KEY'):
        missing.append("OpenAI API key not configured")
    
    return len(missing) == 0, missing

def main():
    usage = """causeway - rule enforcement for Claude Code

Commands:
    setup                Configure email, provider, and API key
    setup --reset        Reset all configuration
    connect              Add hooks & MCP to Claude Code (run from your project)
    config               Show current configuration
    config call-home     Show call-home telemetry status
    config call-home on  Enable call-home telemetry
    config call-home off Disable call-home telemetry
    update               Update to latest release
    update --edge        Update to latest main branch (edge)
    list                 List active rules
    rulesets             List available rulesets
    add <set>            Add a ruleset
    ui                   Start dashboard at localhost:8000
    version              Show version information
"""

    if len(sys.argv) < 2:
        print(usage)
        sys.exit(0)

    cmd = sys.argv[1]

    if cmd in ("--help", "-h", "help"):
        print(usage)
        sys.exit(0)
    elif cmd == "init":
        cmd_init()
    elif cmd == "connect":
        cmd_connect()
    elif cmd == "setup":
        reset = "--reset" in sys.argv
        cmd_setup(reset=reset)
    elif cmd == "config":
        cmd_config(sys.argv[2:])
    elif cmd == "update":
        edge = "--edge" in sys.argv
        cmd_update(edge=edge)
    elif cmd == "rulesets":
        cmd_rulesets()
    elif cmd == "add":
        if len(sys.argv) < 3:
            print("Usage: causeway add <ruleset>")
            cmd_rulesets()
            sys.exit(1)
        cmd_add(sys.argv[2])
    elif cmd == "list":
        cmd_list()
    elif cmd == "ui":
        cmd_ui()
    elif cmd in ("version", "--version", "-v"):
        sys.path.insert(0, str(CAUSEWAY_DIR))
        from version import get_local_version
        print(f"causeway {get_local_version()}")
    else:
        print(f"Unknown command: {cmd}")
        print(usage)
        sys.exit(1)

if __name__ == "__main__":
    main()
