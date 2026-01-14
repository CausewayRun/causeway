"""Tests for history logger."""
import os
import json
import tempfile
import pytest

# Set test database before importing
TEST_DB = tempfile.mktemp(suffix='.db')
os.environ['CAUSEWAY_DB'] = TEST_DB

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import init_db, get_connection
from history_logger import (
    log_transcript, get_or_create_project, get_or_create_session,
    extract_text_content, extract_tool_calls
)


@pytest.fixture(autouse=True)
def setup_db():
    """Initialize fresh database for each test."""
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)
    init_db()
    yield
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)


def test_extract_text_content_string():
    """Should extract text from string content."""
    assert extract_text_content("Hello world") == "Hello world"


def test_extract_text_content_array():
    """Should extract text from array content."""
    content = [
        {"type": "text", "text": "Hello"},
        {"type": "tool_use", "name": "Bash"},
        {"type": "text", "text": "World"}
    ]
    assert extract_text_content(content) == "Hello World"


def test_extract_tool_calls():
    """Should extract tool calls from content."""
    content = [
        {"type": "text", "text": "Let me run this"},
        {"type": "tool_use", "id": "tool_123", "name": "Bash", "input": {"command": "ls"}}
    ]
    tools = extract_tool_calls(content)
    assert len(tools) == 1
    assert tools[0]['tool'] == 'Bash'
    assert tools[0]['tool_use_id'] == 'tool_123'


def test_get_or_create_project():
    """Should create project and return ID."""
    conn = get_connection()
    project_id = get_or_create_project(conn, '/test/project')

    # Should return same ID on second call
    project_id2 = get_or_create_project(conn, '/test/project')
    assert project_id == project_id2

    # Verify in DB
    row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    assert row['path'] == '/test/project'
    assert row['name'] == 'project'
    conn.close()


def test_get_or_create_session():
    """Should create session and return ID."""
    conn = get_connection()
    project_id = get_or_create_project(conn, '/test/project')
    session_id = get_or_create_session(conn, project_id, 'ext-123', '/path/to/transcript.jsonl')

    # Should return same ID on second call
    session_id2 = get_or_create_session(conn, project_id, 'ext-123', '/path/to/transcript.jsonl')
    assert session_id == session_id2

    # Verify in DB
    row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    assert row['external_id'] == 'ext-123'
    conn.close()


def test_log_transcript():
    """Should log a transcript to database."""
    # Create a mock transcript file
    transcript = [
        {"type": "summary", "summary": "Test session"},
        {
            "type": "user",
            "sessionId": "test-session-123",
            "cwd": "/test/project",
            "uuid": "msg-1",
            "message": {"role": "user", "content": "Hello"},
            "timestamp": "2024-01-01T00:00:00Z"
        },
        {
            "type": "assistant",
            "sessionId": "test-session-123",
            "uuid": "msg-2",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Let me help"},
                    {"type": "tool_use", "id": "tool-1", "name": "Bash", "input": {"command": "ls"}}
                ]
            },
            "timestamp": "2024-01-01T00:00:01Z"
        },
        {
            "type": "user",
            "sessionId": "test-session-123",
            "uuid": "msg-3",
            "message": {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "tool-1", "content": "file1.txt\nfile2.txt"}
                ]
            },
            "timestamp": "2024-01-01T00:00:02Z"
        }
    ]

    # Write to temp file
    with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
        for entry in transcript:
            f.write(json.dumps(entry) + '\n')
        transcript_path = f.name

    try:
        # Log it
        stats = log_transcript(transcript_path)

        assert stats['messages'] == 3
        assert stats['tool_calls'] == 1

        # Verify in database
        conn = get_connection()

        # Check session
        session = conn.execute("SELECT * FROM sessions WHERE external_id = 'test-session-123'").fetchone()
        assert session is not None
        assert session['task'] == 'Hello'

        # Check messages
        messages = conn.execute("SELECT * FROM messages WHERE session_id = ?", (session['id'],)).fetchall()
        assert len(messages) == 3

        # Check tool calls
        tool_calls = conn.execute("""
            SELECT tc.* FROM tool_calls tc
            JOIN messages m ON tc.message_id = m.id
            WHERE m.session_id = ?
        """, (session['id'],)).fetchall()
        assert len(tool_calls) == 1
        assert tool_calls[0]['tool'] == 'Bash'
        assert 'file1.txt' in tool_calls[0]['output']

        conn.close()
    finally:
        os.unlink(transcript_path)


def test_log_transcript_idempotent():
    """Logging same transcript twice should skip already-logged messages."""
    transcript = [
        {
            "type": "user",
            "sessionId": "test-123",
            "cwd": "/test",
            "uuid": "msg-1",
            "message": {"role": "user", "content": "Hello"},
            "timestamp": "2024-01-01T00:00:00Z"
        }
    ]

    with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
        for entry in transcript:
            f.write(json.dumps(entry) + '\n')
        transcript_path = f.name

    try:
        # First log
        stats1 = log_transcript(transcript_path)
        assert stats1['messages'] == 1
        assert stats1['skipped'] == 0

        # Second log - should skip
        stats2 = log_transcript(transcript_path)
        assert stats2['messages'] == 0
        assert stats2['skipped'] == 1
    finally:
        os.unlink(transcript_path)
