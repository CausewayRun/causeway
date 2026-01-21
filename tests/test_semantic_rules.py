"""Tests for semantic rules and embeddings in rule_agent."""
import os
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


class TestRuleDecisionModel:
    """Test RuleDecision pydantic model."""

    def test_rule_decision_approved(self):
        """RuleDecision can represent approval."""
        from causeway.rule_agent import RuleDecision

        decision = RuleDecision(
            approved=True,
            action='allow',
            comment='No violations'
        )

        assert decision.approved is True
        assert decision.action == 'allow'

    def test_rule_decision_blocked(self):
        """RuleDecision can represent block."""
        from causeway.rule_agent import RuleDecision

        decision = RuleDecision(
            approved=False,
            action='block',
            comment='Blocked by rule #5'
        )

        assert decision.approved is False
        assert decision.action == 'block'

    def test_rule_decision_warn(self):
        """RuleDecision can represent warning."""
        from causeway.rule_agent import RuleDecision

        decision = RuleDecision(
            approved=False,
            action='warn',
            comment='Warning from rule #3'
        )

        assert decision.action == 'warn'


class TestMatchesPatterns:
    """Test patterns JSON array matching."""

    def test_matches_patterns_single(self):
        """Match single pattern in array."""
        from causeway.rule_agent import matches_patterns

        result = matches_patterns('pip install requests', '["^pip"]')
        assert result is True

    def test_matches_patterns_multiple(self):
        """Match any pattern in array."""
        from causeway.rule_agent import matches_patterns

        patterns = '["main\\\\.py", "config\\\\.py", "auth/"]'

        assert matches_patterns('main.py', patterns) is True
        assert matches_patterns('config.py', patterns) is True
        assert matches_patterns('auth/login.py', patterns) is True
        assert matches_patterns('other.py', patterns) is False

    def test_matches_patterns_none(self):
        """Return False for None patterns."""
        from causeway.rule_agent import matches_patterns

        result = matches_patterns('any input', None)
        assert result is False

    def test_matches_patterns_empty_json(self):
        """Return False for empty array."""
        from causeway.rule_agent import matches_patterns

        result = matches_patterns('any input', '[]')
        assert result is False

    def test_matches_patterns_invalid_json(self):
        """Return False for invalid JSON."""
        from causeway.rule_agent import matches_patterns

        result = matches_patterns('any input', 'not valid json')
        assert result is False

    def test_matches_patterns_invalid_regex(self):
        """Return False for invalid regex in patterns."""
        from causeway.rule_agent import matches_patterns

        result = matches_patterns('any input', '["[invalid(regex"]')
        assert result is False

    def test_matches_patterns_case_insensitive(self):
        """Patterns match case-insensitively."""
        from causeway.rule_agent import matches_patterns

        result = matches_patterns('PIP install', '["pip"]')
        assert result is True


class TestGetSetting:
    """Test settings retrieval."""

    def test_get_setting_returns_default(self):
        """Get setting returns default when not set."""
        from causeway.rule_agent import get_setting

        result = get_setting('nonexistent_key', 'default_value')
        assert result == 'default_value'

    def test_get_setting_from_db(self):
        """Get setting returns value from database."""
        conn = get_connection()
        conn.execute("INSERT INTO settings (key, value) VALUES (?, ?)",
                    ('eval_model', 'custom-model'))
        conn.commit()
        conn.close()

        from causeway.rule_agent import get_setting

        result = get_setting('eval_model', 'default')
        assert result == 'custom-model'


class TestEnsureRuleEmbedding:
    """Test embedding generation for rules."""

    def test_ensure_rule_embedding_creates_new(self):
        """Ensure embedding creates embedding for rule without one."""
        # Create a rule
        conn = get_connection()
        cursor = conn.execute(
            "INSERT INTO rules (type, description, action) VALUES (?, ?, ?)",
            ('semantic', 'Test rule', 'warn')
        )
        rule_id = cursor.lastrowid
        conn.commit()
        conn.close()

        from causeway.rule_agent import ensure_rule_embedding

        with patch('causeway.rule_agent.generate_embedding') as mock_embed:
            mock_embed.return_value = [0.1] * 384

            ensure_rule_embedding(rule_id, 'Test rule description')

            mock_embed.assert_called_once()

            # Verify embedding was inserted
            conn = get_connection()
            row = conn.execute(
                "SELECT * FROM rule_embeddings WHERE rule_id = ?",
                (rule_id,)
            ).fetchone()
            conn.close()

            assert row is not None

    def test_ensure_rule_embedding_skips_existing(self):
        """Ensure embedding doesn't recreate existing embedding."""
        from causeway.rule_agent import ensure_rule_embedding
        from causeway.db import serialize_vector

        # Create a rule with embedding
        conn = get_connection()
        cursor = conn.execute(
            "INSERT INTO rules (type, description, action) VALUES (?, ?, ?)",
            ('semantic', 'Test rule', 'warn')
        )
        rule_id = cursor.lastrowid

        embedding_bytes = serialize_vector([0.1] * 384)
        conn.execute(
            "INSERT INTO rule_embeddings (rule_id, embedding) VALUES (?, ?)",
            (rule_id, embedding_bytes)
        )
        conn.commit()
        conn.close()

        with patch('causeway.rule_agent.generate_embedding') as mock_embed:
            ensure_rule_embedding(rule_id, 'Test rule')

            # Should not call generate_embedding since embedding exists
            mock_embed.assert_not_called()


class TestSyncAllRuleEmbeddings:
    """Test bulk embedding synchronization."""

    def test_sync_all_rule_embeddings(self):
        """Sync generates embeddings for rules without them."""
        from causeway.rule_agent import sync_all_rule_embeddings

        # Create rules without embeddings
        conn = get_connection()
        conn.execute(
            "INSERT INTO rules (type, description, action, active) VALUES (?, ?, ?, ?)",
            ('semantic', 'Rule 1', 'warn', 1)
        )
        conn.execute(
            "INSERT INTO rules (type, description, action, active) VALUES (?, ?, ?, ?)",
            ('semantic', 'Rule 2', 'block', 1)
        )
        conn.commit()
        conn.close()

        with patch('causeway.rule_agent.ensure_rule_embedding') as mock_ensure:
            sync_all_rule_embeddings()

            # Should be called for at least our 2 rules (may have default rules too)
            assert mock_ensure.call_count >= 2


class TestGenerateEmbedding:
    """Test embedding generation."""

    def test_generate_embedding_calls_openai(self):
        """Generate embedding calls OpenAI API."""
        from causeway.rule_agent import generate_embedding

        mock_response = MagicMock()
        mock_response.data = [MagicMock(embedding=[0.1] * 384)]

        with patch('causeway.rule_agent.get_openai_client') as mock_client:
            mock_client.return_value.embeddings.create.return_value = mock_response

            result = generate_embedding('test text')

            assert len(result) == 384
            mock_client.return_value.embeddings.create.assert_called_once()


class TestFindSemanticRules:
    """Test semantic rule finding."""

    def test_find_semantic_rules_keyword_match(self):
        """Find rules with keyword match."""
        # Create a semantic rule
        conn = get_connection()
        conn.execute(
            "INSERT INTO rules (type, description, action, active) VALUES (?, ?, ?, ?)",
            ('semantic', 'Use typescript for all code', 'warn', 1)
        )
        conn.commit()
        conn.close()

        from causeway.rule_agent import find_semantic_rules

        with patch('causeway.rule_agent.generate_embedding') as mock_embed:
            mock_embed.return_value = [0.1] * 384

            # Input contains keyword "typescript"
            rules = find_semantic_rules('Write', 'Writing typescript file')

            # Should find the rule via keyword match
            assert len(rules) >= 1
            assert any('typescript' in r['description'].lower() for r in rules)

    def test_find_semantic_rules_tool_filter(self):
        """Find rules filters by tool."""
        # Create rules for different tools
        conn = get_connection()
        conn.execute(
            "INSERT INTO rules (type, description, action, active, tool) VALUES (?, ?, ?, ?, ?)",
            ('semantic', 'Bash specific rule', 'warn', 1, 'Bash')
        )
        conn.execute(
            "INSERT INTO rules (type, description, action, active, tool) VALUES (?, ?, ?, ?, ?)",
            ('semantic', 'Write specific rule', 'warn', 1, 'Write')
        )
        conn.commit()
        conn.close()

        from causeway.rule_agent import find_semantic_rules

        with patch('causeway.rule_agent.generate_embedding') as mock_embed:
            mock_embed.return_value = [0.1] * 384

            # Should only find Bash rule
            bash_rules = find_semantic_rules('Bash', 'rule')
            assert all(r.get('tool') in (None, 'Bash') for r in bash_rules if 'tool' in r)


class TestCheckWithAgent:
    """Test the main rule checking function."""

    @pytest.mark.asyncio
    async def test_check_with_agent_allows_safe_input(self):
        """Check with agent allows input with no rule violations."""
        from causeway.rule_agent import check_with_agent

        with patch('causeway.rule_agent.check_regex_rules') as mock_regex:
            with patch('causeway.rule_agent.find_semantic_rules') as mock_semantic:
                mock_regex.return_value = (True, None, None, [])
                mock_semantic.return_value = []

                result = await check_with_agent('Bash', 'ls -la')

                assert result.approved is True
                assert result.action == 'allow'

    @pytest.mark.asyncio
    async def test_check_with_agent_blocks_on_regex(self):
        """Check with agent blocks when regex rule matches."""
        from causeway.rule_agent import check_with_agent, RuleDecision

        with patch('causeway.rule_agent.check_regex_rules') as mock_regex:
            mock_regex.return_value = (
                False,
                '[BLOCK #1] Dangerous command',
                'block',
                []
            )

            result = await check_with_agent('Bash', 'rm -rf /')

            assert result.approved is False
            assert result.action == 'block'

    @pytest.mark.asyncio
    async def test_check_with_agent_llm_review(self):
        """Check with agent calls LLM for semantic rules."""
        from causeway.rule_agent import check_with_agent, RuleDecision

        with patch('causeway.rule_agent.check_regex_rules') as mock_regex:
            with patch('causeway.rule_agent.find_semantic_rules') as mock_semantic:
                with patch('causeway.rule_agent.check_rules_with_llm', new_callable=AsyncMock) as mock_llm:
                    mock_regex.return_value = (True, None, None, [])
                    mock_semantic.return_value = [
                        {'id': 1, 'description': 'Use uv', 'action': 'warn',
                         'distance': 0.3, 'match_type': 'keyword'}
                    ]
                    mock_llm.return_value = RuleDecision(
                        approved=False,
                        action='warn',
                        comment='Consider using uv'
                    )

                    result = await check_with_agent('Bash', 'pip install x')

                    mock_llm.assert_called_once()
                    assert result.action == 'warn'


class TestCheckLlmReview:
    """Test LLM review function."""

    @pytest.mark.asyncio
    async def test_check_llm_review_empty_rules(self):
        """LLM review returns allow for empty rules."""
        from causeway.rule_agent import check_llm_review

        result = await check_llm_review([], 'Bash', 'ls')

        assert result.approved is True
        assert result.action == 'allow'

    @pytest.mark.asyncio
    async def test_check_llm_review_calls_agent(self):
        """LLM review calls pydantic-ai agent."""
        from causeway.rule_agent import check_llm_review, RuleDecision

        rules = [
            {'id': 1, 'description': 'Test rule', 'action': 'warn', 'prompt': 'Check this'}
        ]

        with patch('causeway.rule_agent.get_rule_agent') as mock_get_agent:
            mock_agent = MagicMock()
            mock_result = MagicMock()
            mock_result.output = RuleDecision(approved=True, action='allow', comment='OK')
            mock_agent.run = AsyncMock(return_value=mock_result)
            mock_get_agent.return_value = mock_agent

            result = await check_llm_review(rules, 'Bash', 'test input')

            mock_agent.run.assert_called_once()
            assert result.approved is True


class TestCheckRulesWithLlm:
    """Test combined LLM rule check."""

    @pytest.mark.asyncio
    async def test_check_rules_with_llm_includes_justification(self):
        """Check rules with LLM includes justification in prompt."""
        from causeway.rule_agent import check_rules_with_llm, RuleDecision

        rules = [
            {'id': 1, 'description': 'Rule 1', 'action': 'warn'}
        ]

        with patch('causeway.rule_agent.get_rule_agent') as mock_get_agent:
            mock_agent = MagicMock()
            mock_result = MagicMock()
            mock_result.output = RuleDecision(approved=True, action='allow', comment='OK')
            mock_agent.run = AsyncMock(return_value=mock_result)
            mock_get_agent.return_value = mock_agent

            await check_rules_with_llm(
                rules, 'Bash', 'test input',
                justification='OVERRIDE: Testing purposes'
            )

            # Check that justification was included in the prompt
            call_args = mock_agent.run.call_args[0][0]
            assert 'OVERRIDE: Testing purposes' in call_args

    @pytest.mark.asyncio
    async def test_check_rules_with_llm_limits_rules(self):
        """Check rules with LLM limits to 5 rules."""
        from causeway.rule_agent import check_rules_with_llm, RuleDecision

        # Create 10 rules
        rules = [
            {'id': i, 'description': f'Rule {i}', 'action': 'warn'}
            for i in range(10)
        ]

        with patch('causeway.rule_agent.get_rule_agent') as mock_get_agent:
            mock_agent = MagicMock()
            mock_result = MagicMock()
            mock_result.output = RuleDecision(approved=True, action='allow', comment='OK')
            mock_agent.run = AsyncMock(return_value=mock_result)
            mock_get_agent.return_value = mock_agent

            await check_rules_with_llm(rules, 'Bash', 'test')

            # Check that prompt doesn't include all 10 rules
            call_args = mock_agent.run.call_args[0][0]
            # Should have at most 5 rule lines
            rule_lines = [l for l in call_args.split('\n') if l.strip().startswith('- ')]
            assert len(rule_lines) <= 5


class TestCheckSemanticRules:
    """Test semantic rule checking."""

    @pytest.mark.asyncio
    async def test_check_semantic_rules_no_rules(self):
        """Check semantic rules returns allow when no rules found."""
        from causeway.rule_agent import check_semantic_rules

        with patch('causeway.rule_agent.find_semantic_rules') as mock_find:
            mock_find.return_value = []

            result = await check_semantic_rules('Bash', 'ls')

            assert result.approved is True
            assert result.action == 'allow'

    @pytest.mark.asyncio
    async def test_check_semantic_rules_with_close_match(self):
        """Check semantic rules calls LLM for close matches."""
        from causeway.rule_agent import check_semantic_rules, RuleDecision

        with patch('causeway.rule_agent.find_semantic_rules') as mock_find:
            with patch('causeway.rule_agent.get_rule_agent') as mock_get_agent:
                mock_find.return_value = [
                    {'id': 1, 'description': 'Use uv', 'action': 'warn',
                     'distance': 0.3, 'match_type': 'vector'}
                ]

                mock_agent = MagicMock()
                mock_result = MagicMock()
                mock_result.output = RuleDecision(
                    approved=False, action='warn', comment='Use uv instead'
                )
                mock_agent.run = AsyncMock(return_value=mock_result)
                mock_get_agent.return_value = mock_agent

                result = await check_semantic_rules('Bash', 'pip install')

                mock_agent.run.assert_called_once()
                assert result.action == 'warn'


class TestGetRuleAgent:
    """Test rule agent initialization."""

    def test_get_rule_agent_returns_agent(self):
        """Get rule agent returns pydantic-ai Agent."""
        from causeway.rule_agent import get_rule_agent

        with patch('causeway.rule_agent.Agent') as mock_agent_class:
            mock_agent = MagicMock()
            mock_agent_class.return_value = mock_agent

            agent = get_rule_agent()

            mock_agent_class.assert_called_once()
            call_kwargs = mock_agent_class.call_args[1]
            assert 'output_type' in call_kwargs

    def test_get_rule_agent_uses_settings(self):
        """Get rule agent uses settings from database."""
        from causeway.rule_agent import get_rule_agent

        # Set custom settings
        conn = get_connection()
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                    ('eval_model', 'custom-model'))
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                    ('eval_prompt', 'Custom prompt'))
        conn.commit()
        conn.close()

        with patch('causeway.rule_agent.Agent') as mock_agent_class:
            mock_agent = MagicMock()
            mock_agent_class.return_value = mock_agent

            get_rule_agent()

            call_args = mock_agent_class.call_args
            assert call_args[0][0] == 'custom-model'
            assert call_args[1]['system_prompt'] == 'Custom prompt'
