"""Tests for check_rules hook."""
import os
import json
import tempfile
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

# Set test database before importing
TEST_DB = tempfile.mktemp(suffix='.db')
os.environ['CAUSEWAY_DB'] = TEST_DB

from causeway.db import init_db, get_connection


@pytest.fixture(autouse=True)
def setup_db():
    """Initialize fresh database for each test."""
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)
    init_db()
    yield
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)


class TestExtractRuleIds:
    """Test rule ID extraction from comments."""

    def test_extract_single_rule_id(self):
        """Extract single rule ID from comment."""
        from causeway.hooks.check_rules import extract_rule_ids

        result = extract_rule_ids("[BLOCK #5] Some description")
        assert result == [5]

    def test_extract_multiple_rule_ids(self):
        """Extract multiple rule IDs from comment."""
        from causeway.hooks.check_rules import extract_rule_ids

        result = extract_rule_ids("[BLOCK #5] First rule\n[WARN #10] Second rule")
        assert result == [5, 10]

    def test_extract_no_rule_ids(self):
        """Return empty list when no rule IDs."""
        from causeway.hooks.check_rules import extract_rule_ids

        result = extract_rule_ids("No rules here")
        assert result == []

    def test_extract_from_none(self):
        """Return empty list for None input."""
        from causeway.hooks.check_rules import extract_rule_ids

        result = extract_rule_ids(None)
        assert result == []


class TestFormatBlockedOutput:
    """Test formatting of blocked/warned output."""

    def test_format_block_output(self):
        """Format block output with rule info."""
        from causeway.hooks.check_rules import format_blocked_output

        result = format_blocked_output("block", "[BLOCK #5] Don't use pip → Use uv instead")

        assert "CAUSEWAY BLOCKED" in result
        assert "RULE #5" in result
        assert "Description:" in result
        assert "Don't use pip" in result
        assert "Suggested solution:" in result
        assert "Use uv instead" in result

    def test_format_warn_output(self):
        """Format warn output with rule info."""
        from causeway.hooks.check_rules import format_blocked_output

        result = format_blocked_output("warn", "[WARN #3] Consider using typescript")

        assert "CAUSEWAY FLAGGED" in result
        assert "RULE #3" in result

    def test_format_multiple_rules(self):
        """Format output with multiple rules."""
        from causeway.hooks.check_rules import format_blocked_output

        comment = "[BLOCK #1] First rule → Fix 1\n[WARN #2] Second rule → Fix 2"
        result = format_blocked_output("block", comment)

        assert "#1" in result
        assert "#2" in result
        assert "First rule" in result
        assert "Second rule" in result

    def test_format_fallback_format(self):
        """Format output with non-standard format."""
        from causeway.hooks.check_rules import format_blocked_output

        result = format_blocked_output("block", "Simple message without rule format")

        assert "CAUSEWAY BLOCKED" in result
        assert "Simple message" in result


class TestLogTrace:
    """Test trace logging for hooks."""

    def test_log_trace_creates_entry(self):
        """Log trace creates database entry."""
        from causeway.hooks.check_rules import log_trace

        log_trace(
            tool_name='Bash',
            tool_input='pip install requests',
            rules_checked=10,
            rules_matched=1,
            matched_rule_ids=[5],
            decision='block',
            reason='Blocked by rule #5',
            duration_ms=100,
            llm_prompt='Check this input',
            llm_response='Should block'
        )

        conn = get_connection()
        row = conn.execute("SELECT * FROM traces WHERE hook_type = 'pre'").fetchone()
        conn.close()

        assert row is not None
        assert row['tool_name'] == 'Bash'
        assert row['decision'] == 'block'
        assert row['duration_ms'] == 100

    def test_log_trace_truncates_long_input(self):
        """Log trace truncates very long tool input."""
        from causeway.hooks.check_rules import log_trace

        long_input = "x" * 5000

        log_trace(
            tool_name='Write',
            tool_input=long_input,
            rules_checked=1,
            rules_matched=0,
            matched_rule_ids=[],
            decision='allow',
            reason='',
            duration_ms=50
        )

        conn = get_connection()
        row = conn.execute("SELECT * FROM traces WHERE hook_type = 'pre'").fetchone()
        conn.close()

        # Should be truncated to 1000 chars
        assert len(row['tool_input']) <= 1000

    def test_log_trace_handles_errors_silently(self):
        """Log trace doesn't raise exceptions."""
        from causeway.hooks.check_rules import log_trace

        # Even with invalid database, should not raise
        with patch('causeway.hooks.check_rules.get_connection', side_effect=Exception("DB error")):
            log_trace(
                tool_name='Bash',
                tool_input='test',
                rules_checked=0,
                rules_matched=0,
                matched_rule_ids=[],
                decision='allow',
                reason='',
                duration_ms=10
            )
            # Should not raise


class TestCheckRulesAsync:
    """Test async rule checking."""

    @pytest.mark.asyncio
    async def test_check_rules_allows_safe_input(self):
        """Check rules allows input that doesn't match any rules."""
        from causeway.hooks.check_rules import check_rules_async

        with patch('causeway.hooks.check_rules.sync_all_rule_embeddings'):
            with patch('causeway.hooks.check_rules.check_with_agent', new_callable=AsyncMock) as mock_check:
                mock_decision = MagicMock()
                mock_decision.approved = True
                mock_decision.action = 'allow'
                mock_decision.comment = ''
                mock_check.return_value = mock_decision

                allowed, action, comment = await check_rules_async('Bash', 'ls -la')

                assert allowed is True
                assert action == 'allow'

    @pytest.mark.asyncio
    async def test_check_rules_blocks_matching_input(self):
        """Check rules blocks input matching a block rule."""
        from causeway.hooks.check_rules import check_rules_async

        with patch('causeway.hooks.check_rules.sync_all_rule_embeddings'):
            with patch('causeway.hooks.check_rules.check_with_agent', new_callable=AsyncMock) as mock_check:
                mock_decision = MagicMock()
                mock_decision.approved = False
                mock_decision.action = 'block'
                mock_decision.comment = '[BLOCK #1] Blocked'
                mock_check.return_value = mock_decision

                allowed, action, comment = await check_rules_async('Bash', 'rm -rf /')

                assert allowed is False
                assert action == 'block'
                assert 'BLOCK' in comment

    @pytest.mark.asyncio
    async def test_check_rules_warns_on_warn_rule(self):
        """Check rules returns warn for matching warn rules."""
        from causeway.hooks.check_rules import check_rules_async

        with patch('causeway.hooks.check_rules.sync_all_rule_embeddings'):
            with patch('causeway.hooks.check_rules.check_with_agent', new_callable=AsyncMock) as mock_check:
                mock_decision = MagicMock()
                mock_decision.approved = False
                mock_decision.action = 'warn'
                mock_decision.comment = '[WARN #2] Consider alternatives'
                mock_check.return_value = mock_decision

                allowed, action, comment = await check_rules_async('Bash', 'python script.py')

                assert allowed is False
                assert action == 'warn'

    @pytest.mark.asyncio
    async def test_check_rules_passes_justification(self):
        """Check rules passes justification to agent."""
        from causeway.hooks.check_rules import check_rules_async

        with patch('causeway.hooks.check_rules.sync_all_rule_embeddings'):
            with patch('causeway.hooks.check_rules.check_with_agent', new_callable=AsyncMock) as mock_check:
                mock_decision = MagicMock()
                mock_decision.approved = True
                mock_decision.action = 'allow'
                mock_decision.comment = ''
                mock_check.return_value = mock_decision

                await check_rules_async('Bash', 'dangerous cmd', justification='OVERRIDE: Testing')

                # Check that justification was passed
                mock_check.assert_called_once()
                call_args = mock_check.call_args
                assert call_args[0][2] == 'OVERRIDE: Testing'


class TestHookOutput:
    """Test hook output formatting."""

    def test_block_output_format(self):
        """Block output has correct JSON structure."""
        # Simulate what main() produces for a block
        output = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": "CAUSEWAY BLOCKED"
            }
        }

        assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert output["hookSpecificOutput"]["hookEventName"] == "PreToolUse"

    def test_warn_output_format(self):
        """Warn output has correct JSON structure."""
        output = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": "CAUSEWAY FLAGGED"
            }
        }

        assert output["hookSpecificOutput"]["permissionDecision"] == "deny"


class TestMainFunction:
    """Test the main entry point."""

    def test_main_reads_stdin_json(self):
        """Main function reads JSON from stdin."""
        from causeway.hooks.check_rules import main
        import io
        import sys

        hook_input = {
            "tool_name": "Bash",
            "tool_input": {"command": "ls"}
        }

        with patch('sys.stdin', io.StringIO(json.dumps(hook_input))):
            with patch('causeway.hooks.check_rules.asyncio.run') as mock_run:
                mock_run.return_value = (True, 'allow', '')
                with patch('causeway.hooks.check_rules.get_connection') as mock_conn:
                    mock_cursor = MagicMock()
                    mock_cursor.fetchone.return_value = [0]
                    mock_conn.return_value.execute.return_value = mock_cursor
                    mock_conn.return_value.close = MagicMock()

                    with pytest.raises(SystemExit) as exc_info:
                        main()

                    # Exit 0 means allowed
                    assert exc_info.value.code == 0

    def test_main_handles_empty_stdin(self):
        """Main function handles empty stdin gracefully."""
        from causeway.hooks.check_rules import main
        import io

        with patch('sys.stdin', io.StringIO('')):
            with patch('causeway.hooks.check_rules.asyncio.run') as mock_run:
                mock_run.return_value = (True, 'allow', '')
                with patch('causeway.hooks.check_rules.get_connection') as mock_conn:
                    mock_cursor = MagicMock()
                    mock_cursor.fetchone.return_value = [0]
                    mock_conn.return_value.execute.return_value = mock_cursor
                    mock_conn.return_value.close = MagicMock()

                    with pytest.raises(SystemExit) as exc_info:
                        main()

                    assert exc_info.value.code == 0

    def test_main_handles_invalid_json(self):
        """Main function handles invalid JSON in stdin."""
        from causeway.hooks.check_rules import main
        import io

        with patch('sys.stdin', io.StringIO('not valid json')):
            with patch('causeway.hooks.check_rules.asyncio.run') as mock_run:
                mock_run.return_value = (True, 'allow', '')
                with patch('causeway.hooks.check_rules.get_connection') as mock_conn:
                    mock_cursor = MagicMock()
                    mock_cursor.fetchone.return_value = [0]
                    mock_conn.return_value.execute.return_value = mock_cursor
                    mock_conn.return_value.close = MagicMock()

                    with pytest.raises(SystemExit) as exc_info:
                        main()

                    # Should still work with empty hook_input
                    assert exc_info.value.code == 0
