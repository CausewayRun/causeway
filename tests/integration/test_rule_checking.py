"""Integration tests for real LLM rule checking.

These tests make actual API calls to OpenAI for LLM-based rule evaluation.
Run with: uv run pytest tests/integration/test_rule_checking.py -v -m integration
"""
import os
import pytest

# Skip all tests if OPENAI_API_KEY is not set
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get('OPENAI_API_KEY'),
        reason="OPENAI_API_KEY not set"
    )
]


class TestRealRuleChecking:
    """Test real LLM-based rule checking."""

    @pytest.mark.asyncio
    async def test_semantic_rule_blocks_violation(self, test_db, db_connection):
        """Test that semantic rules actually block violations with real LLM."""
        from causeway.rule_agent import ensure_rule_embedding, check_with_agent

        # Insert a real semantic rule
        cursor = db_connection.execute(
            """INSERT INTO rules (type, description, problem, solution, action, active)
               VALUES (?, ?, ?, ?, ?, ?)""",
            ('semantic',
             'Always use uv instead of pip for package management',
             'pip is slower and has dependency resolution issues',
             'Use uv for faster, more reliable package management',
             'block',
             1)
        )
        rule_id = cursor.lastrowid
        db_connection.commit()

        # Generate embedding for the rule
        ensure_rule_embedding(rule_id, "Always use uv instead of pip for package management")

        # This should be blocked by the LLM
        result = await check_with_agent('Bash', 'pip install requests')

        # The LLM should recognize this violates the "use uv instead of pip" rule
        assert result.approved is False or result.action in ('block', 'warn'), \
            f"Expected pip install to be blocked/warned, got: approved={result.approved}, action={result.action}"

    @pytest.mark.asyncio
    async def test_allowed_command_passes(self, test_db, db_connection):
        """Test that unrelated commands pass through when rules don't match."""
        from causeway.rule_agent import ensure_rule_embedding, check_with_agent

        # Insert a rule about pip
        cursor = db_connection.execute(
            """INSERT INTO rules (type, description, action, active)
               VALUES (?, ?, ?, ?)""",
            ('semantic', 'Always use uv instead of pip', 'warn', 1)
        )
        rule_id = cursor.lastrowid
        db_connection.commit()
        ensure_rule_embedding(rule_id, "Always use uv instead of pip")

        # This should pass - it's completely unrelated to pip/uv
        result = await check_with_agent('Bash', 'ls -la /tmp')

        # Should be approved since it's unrelated to the pip rule
        assert result.approved is True, \
            f"Expected 'ls -la' command to be approved, got: approved={result.approved}, comment={result.comment}"

    @pytest.mark.asyncio
    async def test_regex_rule_blocks_pattern(self, test_db, db_connection):
        """Test that regex rules block matching patterns."""
        from causeway.rule_agent import check_with_agent

        # Insert a regex rule
        db_connection.execute(
            """INSERT INTO rules (type, pattern, description, action, active)
               VALUES (?, ?, ?, ?, ?)""",
            ('regex', r'^rm\s+-rf\s+/', 'Block dangerous rm -rf commands', 'block', 1)
        )
        db_connection.commit()

        # This should be blocked by regex
        result = await check_with_agent('Bash', 'rm -rf /important/data')

        assert result.approved is False
        assert result.action == 'block'

    @pytest.mark.asyncio
    async def test_regex_rule_with_llm_review(self, test_db, db_connection):
        """Test regex rule with llm_review flag triggers LLM evaluation."""
        from causeway.rule_agent import check_with_agent

        # Insert a regex rule with llm_review
        db_connection.execute(
            """INSERT INTO rules (type, pattern, description, action, active, llm_review, prompt)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ('regex',
             r'\.env',
             'Review access to .env files',
             'warn',
             1,
             1,  # llm_review = True
             'Check if this access to .env is legitimate for development purposes')
        )
        db_connection.commit()

        # This should trigger LLM review
        result = await check_with_agent('Read', 'cat .env')

        # Result should have a comment from LLM review
        assert result.comment is not None and len(result.comment) > 0

    @pytest.mark.asyncio
    async def test_no_rules_allows_all(self, test_db):
        """Test that commands are allowed when no rules match."""
        from causeway.rule_agent import check_with_agent

        # No rules in database, should allow everything
        result = await check_with_agent('Bash', 'ls -la')

        assert result.approved is True


class TestRealRegexRules:
    """Test regex rule matching with real patterns."""

    @pytest.mark.asyncio
    async def test_multiple_patterns_matching(self, test_db, db_connection):
        """Test that JSON patterns array works correctly."""
        import json
        from causeway.rule_agent import check_with_agent

        # Insert rule with multiple patterns
        patterns = json.dumps([r'main\.py', r'config\.py', r'__init__\.py'])
        db_connection.execute(
            """INSERT INTO rules (type, patterns, description, action, active)
               VALUES (?, ?, ?, ?, ?)""",
            ('regex', patterns, 'Protect core files', 'warn', 1)
        )
        db_connection.commit()

        # Test matching first pattern
        result1 = await check_with_agent('Write', 'main.py')
        assert result1.action == 'warn' or result1.approved is False

        # Test matching second pattern
        result2 = await check_with_agent('Write', 'src/config.py')
        assert result2.action == 'warn' or result2.approved is False

        # Test non-matching
        result3 = await check_with_agent('Write', 'utils.py')
        assert result3.approved is True

    @pytest.mark.asyncio
    async def test_tool_specific_rules(self, test_db, db_connection):
        """Test that tool-specific rules only apply to that tool."""
        from causeway.rule_agent import check_with_agent

        # Rule only for Bash tool
        db_connection.execute(
            """INSERT INTO rules (type, pattern, description, action, active, tool)
               VALUES (?, ?, ?, ?, ?, ?)""",
            ('regex', r'sudo', 'Block sudo in Bash', 'block', 1, 'Bash')
        )
        db_connection.commit()

        # Should block for Bash
        result1 = await check_with_agent('Bash', 'sudo apt-get update')
        assert result1.approved is False

        # Should allow for other tools (sudo in a comment)
        result2 = await check_with_agent('Write', '# Use sudo to install')
        assert result2.approved is True
