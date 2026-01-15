"""DB Manager Agent - maintains schema, migrations, documentation."""
import asyncio
from pathlib import Path
from claude_agent_sdk import tool, create_sdk_mcp_server, query, ClaudeAgentOptions

from .db import get_connection, init_db

SCHEMA_DOC = Path(__file__).parent / "schema.md"


@tool("get_schema", "Get current database schema", {})
async def get_schema(args: dict) -> dict:
    """Return current schema from sqlite_master."""
    conn = get_connection()
    cursor = conn.execute(
        "SELECT type, name, sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type, name"
    )
    rows = cursor.fetchall()
    conn.close()

    schema = "\n\n".join(f"-- {r['type']}: {r['name']}\n{r['sql']}" for r in rows)
    return {"content": [{"type": "text", "text": schema or "No tables found."}]}


@tool("run_migration", "Execute a SQL migration", {"name": str, "sql": str})
async def run_migration(args: dict) -> dict:
    """Run a named migration."""
    conn = get_connection()
    try:
        # Check if already applied
        existing = conn.execute(
            "SELECT 1 FROM migrations WHERE name = ?", (args["name"],)
        ).fetchone()
        if existing:
            return {"content": [{"type": "text", "text": f"Migration '{args['name']}' already applied."}]}

        # Run migration
        conn.executescript(args["sql"])
        conn.execute("INSERT INTO migrations (name) VALUES (?)", (args["name"],))
        conn.commit()
        return {"content": [{"type": "text", "text": f"Migration '{args['name']}' applied successfully."}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Migration failed: {e}"}]}
    finally:
        conn.close()


@tool("update_schema_doc", "Update schema documentation", {"content": str})
async def update_schema_doc(args: dict) -> dict:
    """Update the schema.md documentation file."""
    SCHEMA_DOC.write_text(args["content"])
    return {"content": [{"type": "text", "text": "Schema documentation updated."}]}


@tool("read_schema_doc", "Read current schema documentation", {})
async def read_schema_doc(args: dict) -> dict:
    """Read the schema.md file."""
    if not SCHEMA_DOC.exists():
        return {"content": [{"type": "text", "text": "No schema documentation exists yet."}]}
    return {"content": [{"type": "text", "text": SCHEMA_DOC.read_text()}]}


@tool("list_migrations", "List applied migrations", {})
async def list_migrations(args: dict) -> dict:
    """List all applied migrations."""
    conn = get_connection()
    cursor = conn.execute("SELECT name, applied_at FROM migrations ORDER BY applied_at")
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        return {"content": [{"type": "text", "text": "No migrations applied yet."}]}

    result = "\n".join(f"- {r['name']} ({r['applied_at']})" for r in rows)
    return {"content": [{"type": "text", "text": result}]}


# Create the MCP server
db_manager_server = create_sdk_mcp_server(
    name="db-manager",
    version="1.0.0",
    tools=[get_schema, run_migration, update_schema_doc, read_schema_doc, list_migrations]
)

SYSTEM_PROMPT = """You are the DB Manager for a personal "second brain" SQLite database.

Your responsibilities:
1. Maintain the database schema
2. Run migrations when needed
3. Keep schema.md documentation in sync with actual schema
4. Design tables that help organize thoughts, notes, and knowledge

Always:
- Document schema changes in schema.md with purpose and relationships
- Use migrations for schema changes (never raw ALTER/CREATE)
- Keep the schema minimal and purposeful
"""


async def run_db_manager(user_prompt: str):
    """Run the DB manager agent."""
    init_db()

    async def messages():
        yield {"type": "user", "message": {"role": "user", "content": user_prompt}}

    options = ClaudeAgentOptions(
        system_prompt=SYSTEM_PROMPT,
        mcp_servers={"db-manager": db_manager_server},
        allowed_tools=[
            "mcp__db-manager__get_schema",
            "mcp__db-manager__run_migration",
            "mcp__db-manager__update_schema_doc",
            "mcp__db-manager__read_schema_doc",
            "mcp__db-manager__list_migrations",
        ]
    )

    async for message in query(prompt=messages(), options=options):
        if message.type == "assistant" and hasattr(message, "content"):
            for block in message.content:
                if hasattr(block, "text"):
                    print(block.text)


if __name__ == "__main__":
    import sys
    prompt = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "Show me the current schema and documentation."
    asyncio.run(run_db_manager(prompt))
