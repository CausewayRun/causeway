"""Tests for learning agent."""
import os
import sys
import json
import tempfile
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

# Set test database before importing
TEST_DB = tempfile.mktemp(suffix='_learning.db')
os.environ['CAUSEWAY_DB'] = TEST_DB

from causeway.db import init_db, get_connection

# Create a mock for the call_tool function used by learning_agent
_mock_call_tool = AsyncMock(return_value=[MagicMock(text="OK")])


@pytest.fixture(autouse=True)
def setup_db():
    """Initialize fresh database for each test."""
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)
    init_db()
    yield
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)


class TestRuleChangeModel:
    """Test RuleChange pydantic model."""

    def test_rule_change_create(self):
        """RuleChange model accepts create action."""
        from causeway.learning_agent import RuleChange

        change = RuleChange(
            action="create",
            type="regex",
            pattern="^pip ",
            description="Use uv instead of pip",
            reason="User prefers uv"
        )

        assert change.action == "create"
        assert change.type == "regex"
        assert change.pattern == "^pip "

    def test_rule_change_update(self):
        """RuleChange model accepts update action with rule_id."""
        from causeway.learning_agent import RuleChange

        change = RuleChange(
            action="update",
            rule_id=5,
            description="Updated description",
            reason="Refinement"
        )

        assert change.action == "update"
        assert change.rule_id == 5

    def test_rule_change_delete(self):
        """RuleChange model accepts delete action."""
        from causeway.learning_agent import RuleChange

        change = RuleChange(
            action="delete",
            rule_id=10,
            reason="No longer needed"
        )

        assert change.action == "delete"
        assert change.rule_id == 10


class TestLearningOutput:
    """Test LearningOutput pydantic model."""

    def test_learning_output_with_changes(self):
        """LearningOutput contains list of changes and summary."""
        from causeway.learning_agent import LearningOutput, RuleChange

        change = RuleChange(action="create", description="Test", reason="Test reason")
        output = LearningOutput(
            changes=[change],
            summary="Created 1 rule"
        )

        assert len(output.changes) == 1
        assert output.summary == "Created 1 rule"

    def test_learning_output_empty(self):
        """LearningOutput can have empty changes list."""
        from causeway.learning_agent import LearningOutput

        output = LearningOutput(changes=[], summary="No changes needed")

        assert len(output.changes) == 0


class TestFormatTranscript:
    """Test transcript formatting."""

    def test_format_transcript_simple(self):
        """Format simple user/assistant messages."""
        from causeway.learning_agent import format_transcript

        transcript = [
            {"type": "user", "message": {"role": "user", "content": "Hello"}},
            {"type": "assistant", "message": {"role": "assistant", "content": "Hi there"}}
        ]

        result = format_transcript(transcript)

        assert "USER: Hello" in result
        assert "ASSISTANT: Hi there" in result

    def test_format_transcript_complex_content(self):
        """Format messages with complex content arrays."""
        from causeway.learning_agent import format_transcript

        transcript = [
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "Let me help you"},
                        {"type": "tool_use", "name": "Bash"}
                    ]
                }
            }
        ]

        result = format_transcript(transcript)

        assert "Let me help you" in result
        assert "[Tool: Bash]" in result

    def test_format_transcript_limits_entries(self):
        """Format transcript limits number of entries."""
        from causeway.learning_agent import format_transcript

        # Create more entries than the limit
        transcript = [
            {"type": "user", "message": {"role": "user", "content": f"Message {i}"}}
            for i in range(50)
        ]

        result = format_transcript(transcript, max_entries=10)

        # Should only contain last 10 entries
        assert "Message 49" in result
        assert "Message 40" in result
        assert "Message 0" not in result

    def test_format_transcript_limits_chars(self):
        """Format transcript limits total characters."""
        from causeway.learning_agent import format_transcript

        transcript = [
            {"type": "user", "message": {"role": "user", "content": "x" * 1000}}
            for _ in range(20)
        ]

        result = format_transcript(transcript, max_chars=2000)

        assert len(result) <= 2500  # Some overhead for role prefixes

    def test_format_transcript_skips_tool_results(self):
        """Format transcript skips verbose tool results."""
        from causeway.learning_agent import format_transcript

        transcript = [
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "Reading file"},
                        {"type": "tool_result", "content": "Very long content..."}
                    ]
                }
            }
        ]

        result = format_transcript(transcript)

        assert "Reading file" in result
        assert "Very long content" not in result


class TestGetSetting:
    """Test settings retrieval."""

    def test_get_setting_default(self):
        """Get setting returns default when not set."""
        from causeway.learning_agent import get_setting

        result = get_setting('nonexistent_key', 'default_value')
        assert result == 'default_value'

    def test_get_setting_from_db(self):
        """Get setting returns value from database."""
        conn = get_connection()
        conn.execute("INSERT INTO settings (key, value) VALUES (?, ?)", ('test_key', 'db_value'))
        conn.commit()
        conn.close()

        from causeway.learning_agent import get_setting

        result = get_setting('test_key', 'default')
        assert result == 'db_value'


class TestLogTrace:
    """Test trace logging."""

    def test_log_trace_creates_entry(self):
        """Log trace creates database entry."""
        from causeway.learning_agent import log_trace

        log_trace(
            transcript_path='/test/path/transcript.jsonl',
            rules_created=2,
            rules_updated=1,
            rules_deleted=0,
            llm_prompt='Test prompt',
            llm_response='Test response',
            duration_ms=500
        )

        conn = get_connection()
        row = conn.execute("SELECT * FROM traces WHERE hook_type = 'stop'").fetchone()
        conn.close()

        assert row is not None
        assert row['tool_name'] == 'learning_agent'
        assert row['decision'] == 'learn'
        assert row['duration_ms'] == 500


class TestCreateRule:
    """Test rule creation helper."""

    @pytest.mark.asyncio
    @patch('causeway.learning_agent.call_tool', new_callable=AsyncMock)
    async def test_create_rule_calls_mcp(self, mock_call):
        """Create rule calls MCP tool with correct args."""
        mock_call.return_value = [MagicMock(text="Rule 1 created")]

        # Import after patching
        from causeway.learning_agent import create_rule

        result = await create_rule(
            rule_type='regex',
            description='Test rule',
            pattern='^pip ',
            action='block'
        )

        mock_call.assert_called_once()
        call_args = mock_call.call_args
        assert call_args[0][0] == 'add_rule'
        assert call_args[0][1]['type'] == 'regex'
        assert call_args[0][1]['description'] == 'Test rule'


class TestUpdateRule:
    """Test rule update helper."""

    @pytest.mark.asyncio
    async def test_update_rule_calls_mcp(self):
        """Update rule calls MCP tool with correct args."""
        from causeway.learning_agent import update_rule

        with patch('causeway.learning_agent.call_tool', new_callable=AsyncMock) as mock_call:
            mock_call.return_value = [MagicMock(text="Rule updated")]

            result = await update_rule(
                rule_id=5,
                description='New description'
            )

            mock_call.assert_called_once()
            call_args = mock_call.call_args
            assert call_args[0][0] == 'update_rule'
            assert call_args[0][1]['id'] == 5


class TestDeleteRule:
    """Test rule deletion helper."""

    @pytest.mark.asyncio
    async def test_delete_rule_calls_mcp(self):
        """Delete rule calls MCP tool with rule ID."""
        from causeway.learning_agent import delete_rule

        with patch('causeway.learning_agent.call_tool', new_callable=AsyncMock) as mock_call:
            mock_call.return_value = [MagicMock(text="Rule deleted")]

            result = await delete_rule(rule_id=10)

            mock_call.assert_called_once()
            call_args = mock_call.call_args
            assert call_args[0][0] == 'delete_rule'
            assert call_args[0][1]['id'] == 10


class TestProcessTranscript:
    """Test transcript processing."""

    @pytest.mark.asyncio
    async def test_process_transcript_no_changes(self):
        """Process transcript with no changes returns empty result."""
        from causeway.learning_agent import process_transcript, LearningOutput

        with patch('causeway.learning_agent.extract_rule_changes', new_callable=AsyncMock) as mock_extract:
            mock_extract.return_value = (
                LearningOutput(changes=[], summary="No changes"),
                "prompt",
                "response"
            )

            result, prompt, response, stats = await process_transcript([])

            assert "No rule changes" in result
            assert stats['created'] == 0
            assert stats['updated'] == 0
            assert stats['deleted'] == 0

    @pytest.mark.asyncio
    async def test_process_transcript_with_create(self):
        """Process transcript creates rules."""
        from causeway.learning_agent import process_transcript, LearningOutput, RuleChange

        change = RuleChange(
            action="create",
            type="regex",
            description="Test rule",
            pattern="^test",
            reason="Test"
        )

        with patch('causeway.learning_agent.extract_rule_changes', new_callable=AsyncMock) as mock_extract:
            with patch('causeway.learning_agent.create_rule', new_callable=AsyncMock) as mock_create:
                mock_extract.return_value = (
                    LearningOutput(changes=[change], summary="1 rule"),
                    "prompt",
                    "response"
                )
                mock_create.return_value = "Rule created with ID 1"

                result, prompt, response, stats = await process_transcript([])

                assert stats['created'] == 1
                mock_create.assert_called_once()


class TestGetLearningAgent:
    """Test learning agent initialization."""

    def test_get_learning_agent_returns_agent(self):
        """Get learning agent returns pydantic-ai Agent."""
        from causeway.learning_agent import get_learning_agent

        with patch('causeway.learning_agent.Agent') as mock_agent_class:
            mock_agent = MagicMock()
            mock_agent_class.return_value = mock_agent

            agent = get_learning_agent()

            mock_agent_class.assert_called_once()
            # Check it was called with LearningOutput as output_type
            call_kwargs = mock_agent_class.call_args[1]
            assert 'output_type' in call_kwargs


class TestGetExistingRules:
    """Test existing rules retrieval."""

    @pytest.mark.asyncio
    async def test_get_existing_rules(self):
        """Get existing rules calls MCP list_rules."""
        from causeway.learning_agent import get_existing_rules

        with patch('causeway.learning_agent.call_tool', new_callable=AsyncMock) as mock_call:
            mock_call.return_value = [MagicMock(text="[1] Test rule")]

            result = await get_existing_rules()

            mock_call.assert_called_once_with('list_rules', {'active_only': True})
            assert "[1] Test rule" in result
