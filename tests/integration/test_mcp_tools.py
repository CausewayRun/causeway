"""Integration tests for MCP tool handlers.

These tests verify MCP tools work with real database and embeddings.
Run with: uv run pytest tests/integration/test_mcp_tools.py -v -m integration
"""
import os
import pytest

# Skip if MCP is not available
try:
    from causeway.mcp import is_mcp_available, call_tool, list_tools
    MCP_AVAILABLE = is_mcp_available()
except (ImportError, SystemExit):
    MCP_AVAILABLE = False

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not MCP_AVAILABLE, reason="MCP not available")
]


class TestMCPRuleTools:
    """Test MCP rule management tools with real database."""

    @pytest.mark.asyncio
    async def test_add_rule_creates_embedding(self, test_db, db_connection):
        """Test that add_rule creates rule with embedding."""
        # Skip if no OpenAI key (needed for embeddings)
        if not os.environ.get('OPENAI_API_KEY'):
            pytest.skip("OPENAI_API_KEY not set")

        result = await call_tool('add_rule', {
            'type': 'semantic',
            'description': 'Integration test rule - prefer async/await',
            'problem': 'Callback-based code is harder to read',
            'solution': 'Use async/await patterns for cleaner code',
            'action': 'warn'
        })

        # Should succeed
        assert len(result) == 1
        assert "added" in result[0].text.lower()

        # Verify rule exists
        row = db_connection.execute(
            "SELECT * FROM rules WHERE description LIKE '%Integration test rule%'"
        ).fetchone()
        assert row is not None

        # Verify embedding was created
        emb_row = db_connection.execute(
            "SELECT rule_id FROM rule_embeddings WHERE rule_id = ?",
            (row['id'],)
        ).fetchone()
        assert emb_row is not None

    @pytest.mark.asyncio
    async def test_list_rules_shows_rules(self, test_db, db_connection):
        """Test list_rules returns database rules."""
        # Create test rule directly in DB
        db_connection.execute(
            """INSERT INTO rules (type, description, action, active)
               VALUES (?, ?, ?, ?)""",
            ('regex', 'MCP integration test rule', 'block', 1)
        )
        db_connection.commit()

        result = await call_tool('list_rules', {'active_only': True})

        assert len(result) == 1
        assert "MCP integration test rule" in result[0].text

    @pytest.mark.asyncio
    async def test_update_rule_changes_data(self, test_db, db_connection):
        """Test update_rule modifies rule correctly."""
        # Create rule
        cursor = db_connection.execute(
            """INSERT INTO rules (type, description, action, active)
               VALUES (?, ?, ?, ?)""",
            ('regex', 'Original MCP description', 'warn', 1)
        )
        rule_id = cursor.lastrowid
        db_connection.commit()

        # Update via MCP tool
        result = await call_tool('update_rule', {
            'id': rule_id,
            'description': 'Updated MCP description',
            'action': 'block'
        })

        assert "updated" in result[0].text.lower()

        # Verify changes
        row = db_connection.execute(
            "SELECT description, action FROM rules WHERE id = ?",
            (rule_id,)
        ).fetchone()
        assert row['description'] == 'Updated MCP description'
        assert row['action'] == 'block'

    @pytest.mark.asyncio
    async def test_toggle_rule_changes_status(self, test_db, db_connection):
        """Test toggle_rule enables/disables rules."""
        # Create active rule
        cursor = db_connection.execute(
            """INSERT INTO rules (type, description, action, active)
               VALUES (?, ?, ?, ?)""",
            ('regex', 'Toggle test rule', 'warn', 1)
        )
        rule_id = cursor.lastrowid
        db_connection.commit()

        # Disable
        result = await call_tool('toggle_rule', {'id': rule_id, 'active': False})
        assert "disabled" in result[0].text.lower()

        row = db_connection.execute(
            "SELECT active FROM rules WHERE id = ?", (rule_id,)
        ).fetchone()
        assert row['active'] == 0

        # Enable
        result = await call_tool('toggle_rule', {'id': rule_id, 'active': True})
        assert "enabled" in result[0].text.lower()

        row = db_connection.execute(
            "SELECT active FROM rules WHERE id = ?", (rule_id,)
        ).fetchone()
        assert row['active'] == 1

    @pytest.mark.asyncio
    async def test_delete_rule_removes_data(self, test_db, db_connection):
        """Test delete_rule removes rule and embedding."""
        # Create rule
        cursor = db_connection.execute(
            """INSERT INTO rules (type, description, action, active)
               VALUES (?, ?, ?, ?)""",
            ('regex', 'To be deleted', 'warn', 1)
        )
        rule_id = cursor.lastrowid
        db_connection.commit()

        # Delete via MCP tool
        result = await call_tool('delete_rule', {'id': rule_id})
        assert "deleted" in result[0].text.lower()

        # Verify deletion
        row = db_connection.execute(
            "SELECT * FROM rules WHERE id = ?", (rule_id,)
        ).fetchone()
        assert row is None

    @pytest.mark.asyncio
    @pytest.mark.skipif(
        not os.environ.get('OPENAI_API_KEY'),
        reason="OPENAI_API_KEY not set"
    )
    async def test_search_rules_finds_similar(self, test_db, db_connection):
        """Test search_rules finds semantically similar rules."""
        from causeway.rule_agent import ensure_rule_embedding

        # Create rules with embeddings
        rules = [
            ('semantic', 'Use pytest for testing', 'block'),
            ('semantic', 'Prefer TypeScript over JavaScript', 'warn'),
            ('semantic', 'Always handle errors properly', 'warn'),
        ]

        for rtype, desc, action in rules:
            cursor = db_connection.execute(
                """INSERT INTO rules (type, description, action, active)
                   VALUES (?, ?, ?, ?)""",
                (rtype, desc, action, 1)
            )
            rule_id = cursor.lastrowid
            db_connection.commit()
            ensure_rule_embedding(rule_id, desc)

        # Search for testing-related rules
        result = await call_tool('search_rules', {'query': 'unit tests', 'limit': 5})

        # Should find the pytest rule
        assert "pytest" in result[0].text.lower() or "testing" in result[0].text.lower()


class TestMCPThoughtTools:
    """Test MCP thought management tools.

    Note: These tests require the 'thoughts' table which may not exist
    in all database schemas. Skip if table doesn't exist.
    """

    def _thoughts_table_exists(self, db_connection):
        """Check if thoughts table exists."""
        row = db_connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='thoughts'"
        ).fetchone()
        return row is not None

    @pytest.mark.asyncio
    async def test_add_and_get_thought(self, test_db, db_connection):
        """Test adding and retrieving thoughts."""
        if not self._thoughts_table_exists(db_connection):
            pytest.skip("thoughts table not available")

        # Add thought
        result = await call_tool('add_thought', {
            'content': 'Integration test thought content',
            'category': 'testing'
        })

        assert "added" in result[0].text.lower()

        # Extract ID from response
        import re
        match = re.search(r'ID[:\s]+(\d+)', result[0].text)
        assert match, f"Could not find ID in: {result[0].text}"
        thought_id = int(match.group(1))

        # Get thought
        get_result = await call_tool('get_thought', {'id': thought_id})
        assert "Integration test thought content" in get_result[0].text
        assert "testing" in get_result[0].text.lower()

    @pytest.mark.asyncio
    async def test_list_thoughts(self, test_db, db_connection):
        """Test listing thoughts."""
        if not self._thoughts_table_exists(db_connection):
            pytest.skip("thoughts table not available")

        # Create some thoughts directly
        for i in range(3):
            db_connection.execute(
                "INSERT INTO thoughts (content, category) VALUES (?, ?)",
                (f'List test thought {i}', 'list-test')
            )
        db_connection.commit()

        result = await call_tool('list_thoughts', {'category': 'list-test'})

        assert "List test thought" in result[0].text
        # Should show all 3
        assert "0" in result[0].text
        assert "1" in result[0].text
        assert "2" in result[0].text

    @pytest.mark.asyncio
    async def test_brain_stats(self, test_db, db_connection):
        """Test brain statistics."""
        if not self._thoughts_table_exists(db_connection):
            pytest.skip("thoughts table not available")

        # Add some data
        db_connection.execute(
            "INSERT INTO thoughts (content, category) VALUES (?, ?)",
            ('Stats test thought', 'stats')
        )
        db_connection.commit()

        result = await call_tool('brain_stats', {})

        assert "Total thoughts" in result[0].text
        assert "Categories" in result[0].text
