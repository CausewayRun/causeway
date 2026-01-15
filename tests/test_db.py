"""Tests for database schema and migrations."""
import os
import tempfile
import pytest

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


def test_tables_created():
    """All tables should be created."""
    conn = get_connection()
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    table_names = {t[0] for t in tables}
    conn.close()

    expected = {'rules', 'rule_sets', 'rule_embeddings', 'projects',
                'sessions', 'messages', 'tool_calls', 'rule_triggers', 'migrations'}
    assert expected.issubset(table_names)


def test_rules_columns():
    """Rules table should have all columns."""
    conn = get_connection()
    cursor = conn.execute("PRAGMA table_info(rules)")
    columns = {row[1] for row in cursor.fetchall()}
    conn.close()

    expected = {'id', 'type', 'pattern', 'description', 'tool', 'action',
                'active', 'priority', 'problem', 'solution', 'rule_set_id',
                'source_message_id', 'created_at'}
    assert expected.issubset(columns)


def test_default_rule_set():
    """Default rule set should be created."""
    conn = get_connection()
    row = conn.execute("SELECT * FROM rule_sets WHERE name = 'default'").fetchone()
    conn.close()

    assert row is not None
    assert row['name'] == 'default'


def test_insert_rule():
    """Should be able to insert a rule."""
    conn = get_connection()
    cursor = conn.execute(
        "INSERT INTO rules (type, description, action) VALUES (?, ?, ?)",
        ('semantic', 'Test rule', 'warn')
    )
    rule_id = cursor.lastrowid
    conn.commit()

    row = conn.execute("SELECT * FROM rules WHERE id = ?", (rule_id,)).fetchone()
    conn.close()

    assert row['description'] == 'Test rule'
    assert row['action'] == 'warn'
    assert row['type'] == 'semantic'


def test_insert_project_session_message():
    """Should be able to insert project -> session -> message chain."""
    conn = get_connection()

    # Insert project
    cursor = conn.execute(
        "INSERT INTO projects (path, name) VALUES (?, ?)",
        ('/test/path', 'test-project')
    )
    project_id = cursor.lastrowid

    # Insert session
    cursor = conn.execute(
        "INSERT INTO sessions (project_id, external_id, task) VALUES (?, ?, ?)",
        (project_id, 'abc-123', 'Test task')
    )
    session_id = cursor.lastrowid

    # Insert message
    cursor = conn.execute(
        "INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)",
        (session_id, 'user', 'Hello world')
    )
    message_id = cursor.lastrowid

    # Insert tool call
    cursor = conn.execute(
        "INSERT INTO tool_calls (message_id, tool, input, success) VALUES (?, ?, ?, ?)",
        (message_id, 'Bash', '{"command": "ls"}', 1)
    )
    tool_call_id = cursor.lastrowid

    conn.commit()

    # Verify chain
    row = conn.execute("""
        SELECT p.name, s.task, m.content, tc.tool
        FROM projects p
        JOIN sessions s ON s.project_id = p.id
        JOIN messages m ON m.session_id = s.id
        JOIN tool_calls tc ON tc.message_id = m.id
        WHERE tc.id = ?
    """, (tool_call_id,)).fetchone()
    conn.close()

    assert row['name'] == 'test-project'
    assert row['task'] == 'Test task'
    assert row['content'] == 'Hello world'
    assert row['tool'] == 'Bash'
