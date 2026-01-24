"""Tests for rule agent."""
import os
import tempfile
import pytest

# Set test database before importing
TEST_DB = tempfile.mktemp(suffix='.db')
os.environ['CAUSEWAY_DB'] = TEST_DB

from causeway.db import init_db, get_connection
from causeway.rule_agent import check_regex_rules


@pytest.fixture(autouse=True)
def setup_db():
    """Initialize fresh database for each test."""
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)
    init_db()
    yield
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)


def add_regex_rule(pattern: str, description: str, tool: str = None, action: str = 'block'):
    """Helper to add a regex rule."""
    conn = get_connection()
    conn.execute(
        "INSERT INTO rules (type, pattern, description, tool, action) VALUES (?, ?, ?, ?, ?)",
        ('regex', pattern, description, tool, action)
    )
    conn.commit()
    conn.close()


def test_regex_rule_blocks():
    """Regex rule should block matching input."""
    add_regex_rule(r'^pip3? ', 'Block pip commands', 'Bash', 'block')

    passed, reason, action, _ = check_regex_rules('Bash', 'pip install requests')
    assert not passed
    assert 'Block pip commands' in reason
    assert action == 'block'


def test_regex_rule_allows_non_match():
    """Regex rule should allow non-matching input."""
    add_regex_rule(r'^pip3? ', 'Block pip commands', 'Bash', 'block')

    passed, reason, action, _ = check_regex_rules('Bash', 'uv pip install requests')
    assert passed
    assert reason is None


def test_regex_rule_tool_specific():
    """Regex rule should only apply to specified tool."""
    add_regex_rule(r'^pip3? ', 'Block pip commands', 'Bash', 'block')

    # Should not block Edit tool
    passed, reason, action, _ = check_regex_rules('Edit', 'pip install requests')
    assert passed


def test_regex_rule_all_tools():
    """Regex rule with tool=None should apply to all tools."""
    add_regex_rule(r'SECRET', 'Block secrets', None, 'block')

    passed1, _, _, _ = check_regex_rules('Bash', 'echo SECRET=123')
    passed2, _, _, _ = check_regex_rules('Write', 'SECRET_KEY=abc')
    passed3, _, _, _ = check_regex_rules('Edit', 'MY_SECRET')

    assert not passed1
    assert not passed2
    assert not passed3


def test_regex_warn_does_not_block():
    """Regex rule with action=warn should still reject but with warn action."""
    add_regex_rule(r'^python ', 'Use python3', 'Bash', 'warn')

    passed, reason, action, _ = check_regex_rules('Bash', 'python script.py')
    assert not passed  # warn now also rejects
    assert action == 'warn'


def test_multiple_regex_rules():
    """Multiple regex rules should all be checked."""
    add_regex_rule(r'^pip ', 'Block pip', 'Bash', 'block')
    add_regex_rule(r'rm -rf', 'Block rm -rf', 'Bash', 'block')

    passed1, _, _, _ = check_regex_rules('Bash', 'pip install x')
    passed2, _, _, _ = check_regex_rules('Bash', 'rm -rf /')
    passed3, _, _, _ = check_regex_rules('Bash', 'ls -la')

    assert not passed1
    assert not passed2
    assert passed3


def test_multiple_rules_combined_output():
    """Multiple matching rules should return combined message."""
    add_regex_rule(r'secret', 'No secrets', None, 'block')
    add_regex_rule(r'password', 'No passwords', None, 'warn')

    passed, reason, action, _ = check_regex_rules('Bash', 'echo secret password')
    assert not passed
    assert 'No secrets' in reason
    assert 'No passwords' in reason
    assert action == 'block'  # block takes precedence


def test_patterns_array():
    """Test JSON array patterns field."""
    conn = get_connection()
    conn.execute(
        "INSERT INTO rules (type, patterns, description, action, active) VALUES (?, ?, ?, ?, ?)",
        ('regex', '["foo", "bar", "baz"]', 'Block foo/bar/baz', 'block', 1)
    )
    conn.commit()
    conn.close()

    passed1, _, _, _ = check_regex_rules('Bash', 'echo foo')
    passed2, _, _, _ = check_regex_rules('Bash', 'echo bar')
    passed3, _, _, _ = check_regex_rules('Bash', 'echo baz')
    passed4, _, _, _ = check_regex_rules('Bash', 'echo qux')

    assert not passed1
    assert not passed2
    assert not passed3
    assert passed4


def test_llm_review_flag():
    """Rules with llm_review=1 should be returned for LLM processing."""
    conn = get_connection()
    conn.execute(
        "INSERT INTO rules (type, pattern, description, action, active, llm_review, prompt) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ('regex', 'main\\.py', 'Review main.py changes', 'warn', 1, 1, 'Is this safe?')
    )
    conn.commit()
    conn.close()

    passed, reason, action, llm_reviews = check_regex_rules('Write', 'main.py content')
    assert passed  # llm_review rules don't block directly
    assert len(llm_reviews) == 1
    assert llm_reviews[0]['prompt'] == 'Is this safe?'


def test_warn_only_rules():
    """If only warn rules match, action should be warn."""
    add_regex_rule(r'print\(', 'Use Rich', None, 'warn')
    add_regex_rule(r'logger\.info', 'Use structured logging', None, 'warn')

    passed, reason, action, _ = check_regex_rules('Bash', 'python -c "print(1)"')
    assert not passed
    assert action == 'warn'
    assert 'Use Rich' in reason


def test_update_rule_embedding():
    """Test the consolidated update_rule_embedding function."""
    from causeway.rule_agent import update_rule_embedding
    from unittest.mock import patch

    mock_vec = [0.1] * 384
    with patch('causeway.rule_agent.generate_embedding', return_value=mock_vec):
        # 1. Create new embedding
        update_rule_embedding(99, "Test rule description")
        
        conn = get_connection()
        row = conn.execute("SELECT * FROM rule_embeddings WHERE rule_id = 99").fetchone()
        assert row is not None
        assert row['rule_id'] == 99
        
        # 2. Update existing embedding (force=True)
        new_mock_vec = [0.2] * 384
        with patch('causeway.rule_agent.generate_embedding', return_value=new_mock_vec):
            update_rule_embedding(99, "Updated description", force=True)
            row = conn.execute("SELECT * FROM rule_embeddings WHERE rule_id = 99").fetchone()
            # serialize_vector uses float32, so we just check it was updated
            from causeway.db import serialize_vector
            assert row['embedding'] == serialize_vector(new_mock_vec)

        # 3. Don't update if force=False
        newer_mock_vec = [0.3] * 384
        with patch('causeway.rule_agent.generate_embedding', return_value=newer_mock_vec):
            update_rule_embedding(99, "Newer description", force=False)
            row = conn.execute("SELECT * FROM rule_embeddings WHERE rule_id = 99").fetchone()
            assert row['embedding'] == serialize_vector(new_mock_vec)
            
        conn.close()


def test_sync_all_rule_embeddings():
    """Test syncing all missing rule embeddings."""
    from causeway.rule_agent import sync_all_rule_embeddings
    from unittest.mock import patch

    # Add rules without embeddings
    conn = get_connection()
    conn.execute("INSERT INTO rules (id, type, description, active) VALUES (101, 'semantic', 'Desc 101', 1)")
    conn.execute("INSERT INTO rules (id, type, description, active) VALUES (102, 'semantic', 'Desc 102', 1)")
    conn.commit()
    conn.close()

    mock_vec = [0.1] * 384
    with patch('causeway.rule_agent.generate_embedding', return_value=mock_vec):
        sync_all_rule_embeddings()
        
        conn = get_connection()
        count = conn.execute("SELECT COUNT(*) FROM rule_embeddings WHERE rule_id IN (101, 102)").fetchone()[0]
        assert count == 2
        conn.close()
