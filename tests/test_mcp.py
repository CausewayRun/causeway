"""Tests for MCP server.

These tests require the MCP module to be properly importable.
If MCP cannot be imported, these tests will be skipped.
"""
import os
import sys
import tempfile
import pytest
from unittest.mock import patch, MagicMock

# Set unique test database before importing
TEST_DB = tempfile.mktemp(suffix='_mcp.db')
os.environ['CAUSEWAY_DB'] = TEST_DB

# Check if causeway.mcp can be imported (requires mcp package and proper setup)
try:
    from causeway.db import init_db, get_connection
    from causeway.mcp import call_tool, list_tools
    MCP_AVAILABLE = True
except (ImportError, SystemExit) as e:
    MCP_AVAILABLE = False


# Skip all tests in this module if MCP is not available
pytestmark = pytest.mark.skipif(not MCP_AVAILABLE, reason="causeway.mcp not available")


@pytest.fixture(autouse=True)
def setup_db():
    """Initialize fresh database for each test."""
    if not MCP_AVAILABLE:
        yield
        return

    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)
    init_db()
    yield
    if os.path.exists(TEST_DB):
        try:
            os.remove(TEST_DB)
        except OSError:
            pass


class TestListTools:
    """Test list_tools handler."""

    @pytest.mark.asyncio
    async def test_list_tools_returns_all_tools(self):
        """List tools returns all available tools."""
        tools = await list_tools()

        tool_names = [t.name for t in tools]

        # Check thought tools
        assert 'search_thoughts' in tool_names
        assert 'add_thought' in tool_names
        assert 'list_thoughts' in tool_names
        assert 'get_thought' in tool_names
        assert 'update_thought' in tool_names
        assert 'delete_thought' in tool_names
        assert 'list_categories' in tool_names
        assert 'brain_stats' in tool_names

        # Check rule tools
        assert 'list_rules' in tool_names
        assert 'search_rules' in tool_names
        assert 'add_rule' in tool_names
        assert 'update_rule' in tool_names
        assert 'delete_rule' in tool_names
        assert 'toggle_rule' in tool_names


class TestRuleTools:
    """Test rule management MCP tools."""

    @pytest.mark.asyncio
    async def test_list_rules_empty(self):
        """List rules returns message when no rules."""
        result = await call_tool('list_rules', {})

        assert len(result) == 1
        assert "No rules defined" in result[0].text or "Rules" in result[0].text

    @pytest.mark.asyncio
    async def test_add_rule(self):
        """Add rule creates new rule."""
        with patch('causeway.mcp.ensure_rule_embedding'):
            result = await call_tool('add_rule', {
                'type': 'regex',
                'description': 'Test rule via MCP',
                'pattern': '^test',
                'action': 'warn'
            })

            assert len(result) == 1
            assert "Rule added" in result[0].text

            # Verify in database
            conn = get_connection()
            row = conn.execute("SELECT * FROM rules WHERE description = 'Test rule via MCP'").fetchone()
            conn.close()

            assert row is not None
            assert row['type'] == 'regex'
            assert row['action'] == 'warn'

    @pytest.mark.asyncio
    async def test_list_rules_with_data(self):
        """List rules returns rule data."""
        # Add a rule directly to DB
        conn = get_connection()
        conn.execute(
            "INSERT INTO rules (type, description, action, pattern, active) VALUES (?, ?, ?, ?, ?)",
            ('regex', 'MCP test rule', 'block', '^dangerous', 1)
        )
        conn.commit()
        conn.close()

        result = await call_tool('list_rules', {'active_only': True})

        assert len(result) == 1
        assert "MCP test rule" in result[0].text
        assert "block" in result[0].text.lower()

    @pytest.mark.asyncio
    async def test_update_rule(self):
        """Update rule modifies existing rule."""
        # Add a rule
        conn = get_connection()
        cursor = conn.execute(
            "INSERT INTO rules (type, description, action) VALUES (?, ?, ?)",
            ('regex', 'Original desc', 'warn')
        )
        rule_id = cursor.lastrowid
        conn.commit()
        conn.close()

        with patch('causeway.mcp.ensure_rule_embedding'):
            result = await call_tool('update_rule', {
                'id': rule_id,
                'description': 'Updated desc',
                'action': 'block'
            })

            assert "updated" in result[0].text.lower()

            # Verify update
            conn = get_connection()
            row = conn.execute("SELECT * FROM rules WHERE id = ?", (rule_id,)).fetchone()
            conn.close()

            assert row['description'] == 'Updated desc'
            assert row['action'] == 'block'

    @pytest.mark.asyncio
    async def test_update_rule_not_found(self):
        """Update nonexistent rule returns not found."""
        result = await call_tool('update_rule', {
            'id': 99999,
            'description': 'New desc'
        })

        assert "not found" in result[0].text.lower()

    @pytest.mark.asyncio
    async def test_delete_rule(self):
        """Delete rule removes rule from database."""
        # Add a rule
        conn = get_connection()
        cursor = conn.execute(
            "INSERT INTO rules (type, description, action) VALUES (?, ?, ?)",
            ('regex', 'To delete', 'warn')
        )
        rule_id = cursor.lastrowid
        conn.commit()
        conn.close()

        result = await call_tool('delete_rule', {'id': rule_id})

        assert "deleted" in result[0].text.lower()

        # Verify deletion
        conn = get_connection()
        row = conn.execute("SELECT * FROM rules WHERE id = ?", (rule_id,)).fetchone()
        conn.close()

        assert row is None

    @pytest.mark.asyncio
    async def test_toggle_rule(self):
        """Toggle rule changes active status."""
        # Add an active rule
        conn = get_connection()
        cursor = conn.execute(
            "INSERT INTO rules (type, description, action, active) VALUES (?, ?, ?, ?)",
            ('regex', 'Toggle test', 'warn', 1)
        )
        rule_id = cursor.lastrowid
        conn.commit()
        conn.close()

        # Disable
        result = await call_tool('toggle_rule', {'id': rule_id, 'active': False})
        assert "disabled" in result[0].text.lower()

        conn = get_connection()
        row = conn.execute("SELECT active FROM rules WHERE id = ?", (rule_id,)).fetchone()
        conn.close()
        assert row['active'] == 0

        # Enable
        result = await call_tool('toggle_rule', {'id': rule_id, 'active': True})
        assert "enabled" in result[0].text.lower()


class TestUnknownTool:
    """Test unknown tool handling."""

    @pytest.mark.asyncio
    async def test_unknown_tool(self):
        """Unknown tool returns error message."""
        result = await call_tool('nonexistent_tool', {})

        assert len(result) == 1
        assert "Unknown tool" in result[0].text
