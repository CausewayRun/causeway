"""MCP server for nano_brain - search and manage thoughts."""
import json
import sqlite3
from .db import get_connection, init_db, serialize_vector
from .rule_agent import ensure_rule_embedding

# Graceful MCP import - don't exit on import failure
_MCP_AVAILABLE = False
_MCP_IMPORT_ERROR = None
Server = None
stdio_server = None
Tool = None
TextContent = None

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import Tool, TextContent
    _MCP_AVAILABLE = True
except ImportError as e:
    _MCP_IMPORT_ERROR = str(e)


def require_mcp():
    """Raise ImportError if MCP is not available."""
    if not _MCP_AVAILABLE:
        raise ImportError(f"MCP not available: {_MCP_IMPORT_ERROR}. Please install mcp: pip install mcp")


def is_mcp_available():
    """Check if MCP is available."""
    return _MCP_AVAILABLE


# Only create server if MCP is available
server = Server("nano-brain") if _MCP_AVAILABLE else None


def _list_tools_decorator(func):
    """Apply server.list_tools decorator only if MCP is available."""
    if server is not None:
        return server.list_tools()(func)
    return func


@_list_tools_decorator
async def list_tools():
    return [
        Tool(
            name="search_thoughts",
            description="Search thoughts by keyword in content or category",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "category": {"type": "string", "description": "Optional category filter"},
                    "limit": {"type": "integer", "description": "Max results (default 10)"}
                },
                "required": ["query"]
            }
        ),
        Tool(
            name="add_thought",
            description="Add a new thought to the brain",
            inputSchema={
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "The thought content"},
                    "category": {"type": "string", "description": "Optional category"}
                },
                "required": ["content"]
            }
        ),
        Tool(
            name="list_thoughts",
            description="List all thoughts, optionally filtered by category",
            inputSchema={
                "type": "object",
                "properties": {
                    "category": {"type": "string", "description": "Optional category filter"},
                    "limit": {"type": "integer", "description": "Max results (default 20)"}
                }
            }
        ),
        Tool(
            name="get_thought",
            description="Get a specific thought by ID",
            inputSchema={
                "type": "object",
                "properties": {
                    "id": {"type": "integer", "description": "Thought ID"}
                },
                "required": ["id"]
            }
        ),
        Tool(
            name="update_thought",
            description="Update an existing thought",
            inputSchema={
                "type": "object",
                "properties": {
                    "id": {"type": "integer", "description": "Thought ID"},
                    "content": {"type": "string", "description": "New content"},
                    "category": {"type": "string", "description": "New category"}
                },
                "required": ["id"]
            }
        ),
        Tool(
            name="delete_thought",
            description="Delete a thought by ID",
            inputSchema={
                "type": "object",
                "properties": {
                    "id": {"type": "integer", "description": "Thought ID"}
                },
                "required": ["id"]
            }
        ),
        Tool(
            name="list_categories",
            description="List all unique categories",
            inputSchema={
                "type": "object",
                "properties": {}
            }
        ),
        Tool(
            name="brain_stats",
            description="Get statistics about the brain database",
            inputSchema={
                "type": "object",
                "properties": {}
            }
        ),
        # Rule management tools
        Tool(
            name="list_rules",
            description="List all rules. Rules have type 'regex' (pattern-matched) or 'semantic' (embedding-matched)",
            inputSchema={
                "type": "object",
                "properties": {
                    "active_only": {"type": "boolean", "description": "Only show active rules (default true)"},
                    "type": {"type": "string", "description": "Filter by type: 'regex' or 'semantic'"}
                }
            }
        ),
        Tool(
            name="search_rules",
            description="Search rules by description using semantic similarity",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "limit": {"type": "integer", "description": "Max results (default 5)"}
                },
                "required": ["query"]
            }
        ),
        Tool(
            name="add_rule",
            description="Add a new rule. Use type='regex' for pattern matching (fast), type='semantic' for preferences (LLM-based). Set llm_review=true to have LLM evaluate matches.",
            inputSchema={
                "type": "object",
                "properties": {
                    "type": {"type": "string", "description": "'regex' or 'semantic' (default: semantic)"},
                    "pattern": {"type": "string", "description": "Single regex pattern (legacy)"},
                    "patterns": {"type": "string", "description": "JSON array of regex patterns, e.g. [\"main\\\\.py\", \"auth/.*\"]"},
                    "description": {"type": "string", "description": "Short summary of the rule"},
                    "problem": {"type": "string", "description": "What went wrong / what to avoid"},
                    "solution": {"type": "string", "description": "How it was resolved / what to do instead"},
                    "tool": {"type": "string", "description": "Specific tool (Bash, Edit, Write) or omit for all"},
                    "action": {"type": "string", "description": "block (default), warn, or log"},
                    "llm_review": {"type": "boolean", "description": "If true, LLM reviews matched content before action"},
                    "prompt": {"type": "string", "description": "Context for LLM review, e.g. 'Check if this weakens security'"},
                    "source_session_id": {"type": "integer", "description": "Session ID that created this rule (for tracking)"}
                },
                "required": ["description"]
            }
        ),
        Tool(
            name="update_rule",
            description="Update an existing rule",
            inputSchema={
                "type": "object",
                "properties": {
                    "id": {"type": "integer", "description": "Rule ID"},
                    "pattern": {"type": "string", "description": "Single regex pattern"},
                    "patterns": {"type": "string", "description": "JSON array of regex patterns"},
                    "description": {"type": "string", "description": "New description"},
                    "problem": {"type": "string", "description": "New problem description"},
                    "solution": {"type": "string", "description": "New solution description"},
                    "action": {"type": "string", "description": "New action (block/warn/log)"},
                    "llm_review": {"type": "boolean", "description": "If true, LLM reviews matched content"},
                    "prompt": {"type": "string", "description": "Context for LLM review"}
                },
                "required": ["id"]
            }
        ),
        Tool(
            name="delete_rule",
            description="Delete a rule by ID",
            inputSchema={
                "type": "object",
                "properties": {
                    "id": {"type": "integer", "description": "Rule ID"}
                },
                "required": ["id"]
            }
        ),
        Tool(
            name="toggle_rule",
            description="Enable or disable a rule",
            inputSchema={
                "type": "object",
                "properties": {
                    "id": {"type": "integer", "description": "Rule ID"},
                    "active": {"type": "boolean", "description": "true to enable, false to disable"}
                },
                "required": ["id", "active"]
            }
        )
    ]


def _call_tool_decorator(func):
    """Apply server.call_tool decorator only if MCP is available."""
    if server is not None:
        return server.call_tool()(func)
    return func


@_call_tool_decorator
async def call_tool(name: str, arguments: dict):
    conn = get_connection()
    try:
        if name == "search_thoughts":
            query = arguments.get("query", "")
            category = arguments.get("category")
            limit = arguments.get("limit", 10)

            sql = "SELECT id, content, category, created_at FROM thoughts WHERE content LIKE ?"
            params = [f"%{query}%"]

            if category:
                sql += " AND category = ?"
                params.append(category)

            sql += " ORDER BY created_at DESC LIMIT ?"
            params.append(limit)

            rows = conn.execute(sql, params).fetchall()

            if not rows:
                return [TextContent(type="text", text="No thoughts found matching your query.")]

            results = []
            for r in rows:
                results.append(f"[{r['id']}] ({r['category'] or 'uncategorized'}) {r['content'][:200]}...")

            return [TextContent(type="text", text=f"Found {len(rows)} thought(s):\n\n" + "\n\n".join(results))]

        elif name == "add_thought":
            content = arguments["content"]
            category = arguments.get("category")

            cursor = conn.execute(
                "INSERT INTO thoughts (content, category) VALUES (?, ?)",
                (content, category)
            )
            conn.commit()
            thought_id = cursor.lastrowid

            return [TextContent(type="text", text=f"Thought added with ID: {thought_id}")]

        elif name == "list_thoughts":
            category = arguments.get("category")
            limit = arguments.get("limit", 20)

            if category:
                rows = conn.execute(
                    "SELECT id, content, category, created_at FROM thoughts WHERE category = ? ORDER BY created_at DESC LIMIT ?",
                    (category, limit)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, content, category, created_at FROM thoughts ORDER BY created_at DESC LIMIT ?",
                    (limit,)
                ).fetchall()

            if not rows:
                return [TextContent(type="text", text="No thoughts in the brain yet.")]

            results = []
            for r in rows:
                preview = r['content'][:100] + "..." if len(r['content']) > 100 else r['content']
                results.append(f"[{r['id']}] ({r['category'] or 'uncategorized'}) {preview}")

            return [TextContent(type="text", text=f"Thoughts ({len(rows)}):\n\n" + "\n\n".join(results))]

        elif name == "get_thought":
            thought_id = arguments["id"]
            row = conn.execute(
                "SELECT * FROM thoughts WHERE id = ?", (thought_id,)
            ).fetchone()

            if not row:
                return [TextContent(type="text", text=f"Thought {thought_id} not found.")]

            return [TextContent(type="text", text=f"ID: {row['id']}\nCategory: {row['category'] or 'uncategorized'}\nCreated: {row['created_at']}\nUpdated: {row['updated_at']}\n\nContent:\n{row['content']}")]

        elif name == "update_thought":
            thought_id = arguments["id"]
            content = arguments.get("content")
            category = arguments.get("category")

            existing = conn.execute("SELECT * FROM thoughts WHERE id = ?", (thought_id,)).fetchone()
            if not existing:
                return [TextContent(type="text", text=f"Thought {thought_id} not found.")]

            updates = []
            params = []
            if content is not None:
                updates.append("content = ?")
                params.append(content)
            if category is not None:
                updates.append("category = ?")
                params.append(category)

            if updates:
                updates.append("updated_at = CURRENT_TIMESTAMP")
                params.append(thought_id)
                conn.execute(f"UPDATE thoughts SET {', '.join(updates)} WHERE id = ?", params)
                conn.commit()

            return [TextContent(type="text", text=f"Thought {thought_id} updated.")]

        elif name == "delete_thought":
            thought_id = arguments["id"]
            conn.execute("DELETE FROM thoughts WHERE id = ?", (thought_id,))
            conn.execute("DELETE FROM thought_embeddings WHERE thought_id = ?", (thought_id,))
            conn.commit()
            return [TextContent(type="text", text=f"Thought {thought_id} deleted.")]

        elif name == "list_categories":
            rows = conn.execute(
                "SELECT DISTINCT category, COUNT(*) as count FROM thoughts GROUP BY category ORDER BY count DESC"
            ).fetchall()

            if not rows:
                return [TextContent(type="text", text="No categories yet.")]

            results = [f"- {r['category'] or 'uncategorized'}: {r['count']} thought(s)" for r in rows]
            return [TextContent(type="text", text="Categories:\n" + "\n".join(results))]

        elif name == "brain_stats":
            total = conn.execute("SELECT COUNT(*) as c FROM thoughts").fetchone()['c']
            categories = conn.execute("SELECT COUNT(DISTINCT category) as c FROM thoughts").fetchone()['c']
            with_embeddings = conn.execute("SELECT COUNT(*) as c FROM thought_embeddings").fetchone()['c']

            latest = conn.execute("SELECT created_at FROM thoughts ORDER BY created_at DESC LIMIT 1").fetchone()
            latest_date = latest['created_at'] if latest else "N/A"

            return [TextContent(type="text", text=f"Brain Statistics:\n- Total thoughts: {total}\n- Categories: {categories}\n- With embeddings: {with_embeddings}\n- Latest thought: {latest_date}")]

        # Rule management
        elif name == "list_rules":
            active_only = arguments.get("active_only", True)
            rule_type = arguments.get("type")

            sql = "SELECT * FROM rules WHERE 1=1"
            params = []
            if active_only:
                sql += " AND active = 1"
            if rule_type:
                sql += " AND type = ?"
                params.append(rule_type)
            sql += " ORDER BY priority DESC, id"

            rows = conn.execute(sql, params).fetchall()

            if not rows:
                return [TextContent(type="text", text="No rules defined yet.")]

            results = []
            for r in rows:
                status = "ACTIVE" if r['active'] else "DISABLED"
                tool = r['tool'] or "all"
                pattern_str = f"\n    Pattern: {r['pattern']}" if r['pattern'] else ""
                problem_str = f"\n    Problem: {r['problem']}" if r['problem'] else ""
                solution_str = f"\n    Solution: {r['solution']}" if r['solution'] else ""
                results.append(
                    f"[{r['id']}] [{r['type']}] [{status}] ({r['action']}) {tool}\n"
                    f"    {r['description']}{pattern_str}{problem_str}{solution_str}"
                )

            return [TextContent(type="text", text=f"Rules ({len(rows)}):\n\n" + "\n\n".join(results))]

        elif name == "search_rules":
            from .rule_agent import generate_embedding
            query = arguments["query"]
            limit = arguments.get("limit", 5)

            query_embedding = generate_embedding(query)
            embedding_bytes = serialize_vector(query_embedding)

            rows = conn.execute("""
                SELECT r.id, r.type, r.pattern, r.description, r.action, r.tool, re.distance
                FROM rule_embeddings re
                JOIN rules r ON r.id = re.rule_id
                WHERE re.embedding MATCH ? AND re.k = ? AND r.active = 1
                ORDER BY re.distance
            """, (embedding_bytes, limit)).fetchall()

            if not rows:
                return [TextContent(type="text", text="No matching rules found.")]

            results = []
            for r in rows:
                similarity = 1 - r['distance']
                results.append(f"[{r['id']}] [{r['type']}] (sim: {similarity:.2f}) {r['description']}")

            return [TextContent(type="text", text=f"Found {len(rows)} rules:\n\n" + "\n".join(results))]

        elif name == "add_rule":
            rule_type = arguments.get("type", "semantic")
            pattern = arguments.get("pattern")
            patterns = arguments.get("patterns")
            description = arguments["description"]
            problem = arguments.get("problem")
            solution = arguments.get("solution")
            tool = arguments.get("tool")
            action = arguments.get("action", "block")
            llm_review = 1 if arguments.get("llm_review") else 0
            prompt = arguments.get("prompt")
            source_session_id = arguments.get("source_session_id")

            cursor = conn.execute(
                """INSERT INTO rules (type, pattern, patterns, description, problem, solution, tool, action, priority,
                   llm_review, prompt, source_session_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)""",
                (rule_type, pattern, patterns, description, problem, solution, tool, action,
                 llm_review, prompt, source_session_id)
            )
            conn.commit()
            rule_id = cursor.lastrowid

            # Generate embedding for semantic search
            embed_text = description
            if problem:
                embed_text += f" Problem: {problem}"
            if solution:
                embed_text += f" Solution: {solution}"
            ensure_rule_embedding(rule_id, embed_text)

            return [TextContent(type="text", text=f"Rule added with ID: {rule_id}")]

        elif name == "update_rule":
            rule_id = arguments["id"]
            pattern = arguments.get("pattern")
            patterns = arguments.get("patterns")
            description = arguments.get("description")
            problem = arguments.get("problem")
            solution = arguments.get("solution")
            action = arguments.get("action")
            llm_review = arguments.get("llm_review")
            prompt = arguments.get("prompt")

            existing = conn.execute("SELECT * FROM rules WHERE id = ?", (rule_id,)).fetchone()
            if not existing:
                return [TextContent(type="text", text=f"Rule {rule_id} not found.")]

            if pattern is not None:
                conn.execute("UPDATE rules SET pattern = ? WHERE id = ?", (pattern, rule_id))
            if patterns is not None:
                conn.execute("UPDATE rules SET patterns = ? WHERE id = ?", (patterns, rule_id))
            if description is not None:
                conn.execute("UPDATE rules SET description = ? WHERE id = ?", (description, rule_id))
            if problem is not None:
                conn.execute("UPDATE rules SET problem = ? WHERE id = ?", (problem, rule_id))
            if solution is not None:
                conn.execute("UPDATE rules SET solution = ? WHERE id = ?", (solution, rule_id))
            if action is not None:
                conn.execute("UPDATE rules SET action = ? WHERE id = ?", (action, rule_id))
            if llm_review is not None:
                conn.execute("UPDATE rules SET llm_review = ? WHERE id = ?", (1 if llm_review else 0, rule_id))
            if prompt is not None:
                conn.execute("UPDATE rules SET prompt = ? WHERE id = ?", (prompt, rule_id))
            conn.commit()

            # Regenerate embedding if description/problem/solution changed
            if description is not None or problem is not None or solution is not None:
                updated = conn.execute("SELECT description, problem, solution FROM rules WHERE id = ?", (rule_id,)).fetchone()
                embed_text = updated['description']
                if updated['problem']:
                    embed_text += f" Problem: {updated['problem']}"
                if updated['solution']:
                    embed_text += f" Solution: {updated['solution']}"
                conn.execute("DELETE FROM rule_embeddings WHERE rule_id = ?", (rule_id,))
                conn.commit()
                ensure_rule_embedding(rule_id, embed_text)

            return [TextContent(type="text", text=f"Rule {rule_id} updated.")]

        elif name == "delete_rule":
            rule_id = arguments["id"]
            conn.execute("DELETE FROM rules WHERE id = ?", (rule_id,))
            conn.execute("DELETE FROM rule_embeddings WHERE rule_id = ?", (rule_id,))
            conn.commit()
            return [TextContent(type="text", text=f"Rule {rule_id} deleted.")]

        elif name == "toggle_rule":
            rule_id = arguments["id"]
            active = 1 if arguments["active"] else 0
            conn.execute("UPDATE rules SET active = ? WHERE id = ?", (active, rule_id))
            conn.commit()
            status = "enabled" if active else "disabled"
            return [TextContent(type="text", text=f"Rule {rule_id} {status}.")]

        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]

    finally:
        conn.close()


async def main():
    require_mcp()
    init_db()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def run():
    """Entry point for causeway-mcp command."""
    require_mcp()
    import asyncio
    asyncio.run(main())


if __name__ == "__main__":
    run()
