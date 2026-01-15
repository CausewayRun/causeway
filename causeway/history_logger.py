"""Log session history from Claude Code transcripts."""
import json
import os
import re
from pathlib import Path
from .db import get_connection, init_db


def get_or_create_project(conn, project_path: str) -> int:
    """Get or create a project by path."""
    row = conn.execute(
        "SELECT id FROM projects WHERE path = ?", (project_path,)
    ).fetchone()
    if row:
        return row['id']

    # Extract name from path
    name = Path(project_path).name
    cursor = conn.execute(
        "INSERT INTO projects (path, name) VALUES (?, ?)",
        (project_path, name)
    )
    conn.commit()
    return cursor.lastrowid


def get_or_create_session(conn, project_id: int, external_id: str, transcript_path: str) -> int:
    """Get or create a session by external ID."""
    row = conn.execute(
        "SELECT id FROM sessions WHERE external_id = ?", (external_id,)
    ).fetchone()
    if row:
        return row['id']

    cursor = conn.execute(
        "INSERT INTO sessions (project_id, external_id, transcript_path) VALUES (?, ?, ?)",
        (project_id, external_id, transcript_path)
    )
    conn.commit()
    return cursor.lastrowid


def extract_text_content(content) -> str:
    """Extract text from message content (handles string or array)."""
    if isinstance(content, str):
        return content[:2000]  # Truncate

    if isinstance(content, list):
        texts = []
        for item in content:
            if isinstance(item, dict):
                if item.get('type') == 'text':
                    texts.append(item.get('text', ''))
        return ' '.join(texts)[:2000]

    return str(content)[:2000]


def extract_tool_calls(content) -> list:
    """Extract tool_use blocks from message content."""
    if not isinstance(content, list):
        return []

    tools = []
    for item in content:
        if isinstance(item, dict) and item.get('type') == 'tool_use':
            tools.append({
                'tool_use_id': item.get('id'),
                'tool': item.get('name'),
                'input': json.dumps(item.get('input', {}))[:5000],  # Truncate
            })
    return tools


def find_tool_result(transcript: list, tool_use_id: str) -> dict | None:
    """Find the tool_result for a given tool_use_id in the transcript."""
    for entry in transcript:
        msg = entry.get('message', {})
        content = msg.get('content', [])
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get('type') == 'tool_result':
                    if item.get('tool_use_id') == tool_use_id:
                        result_content = item.get('content', '')
                        is_error = item.get('is_error', False)
                        return {
                            'output': str(result_content)[:5000],
                            'success': 0 if is_error else 1,
                            'error_message': str(result_content)[:500] if is_error else None
                        }
    return None


def log_transcript(transcript_path: str, log_fn=None) -> dict:
    """
    Parse a transcript JSONL file and log to database.
    Returns stats about what was logged.
    """
    def log(msg):
        if log_fn:
            log_fn(msg)

    init_db()
    conn = get_connection()

    try:
        # Load transcript
        transcript = []
        with open(transcript_path, 'r') as f:
            for line in f:
                line = line.strip()
                if line:
                    transcript.append(json.loads(line))

        if not transcript:
            return {'error': 'Empty transcript'}

        # Extract session info (find first entry with sessionId)
        session_id_ext = None
        cwd = os.getcwd()
        for entry in transcript:
            if entry.get('sessionId'):
                session_id_ext = entry.get('sessionId')
                cwd = entry.get('cwd', cwd)
                break

        if not session_id_ext:
            return {'error': 'No sessionId in transcript'}

        # Get/create project and session
        project_id = get_or_create_project(conn, cwd)
        session_id = get_or_create_session(conn, project_id, session_id_ext, transcript_path)

        log(f"Logging to session {session_id} (external: {session_id_ext})")

        # Track what we've already logged (by external_id)
        existing_messages = set()
        for row in conn.execute(
            "SELECT external_id FROM messages WHERE session_id = ? AND external_id IS NOT NULL",
            (session_id,)
        ).fetchall():
            existing_messages.add(row['external_id'])

        stats = {'messages': 0, 'tool_calls': 0, 'skipped': 0}

        # Extract first user message as task
        for entry in transcript:
            if entry.get('type') == 'user':
                msg = entry.get('message', {})
                content = msg.get('content', '')
                if isinstance(content, str) and content.strip():
                    task = content[:200]
                    conn.execute(
                        "UPDATE sessions SET task = ? WHERE id = ? AND task IS NULL",
                        (task, session_id)
                    )
                    conn.commit()
                    break

        # Process each entry
        for entry in transcript:
            entry_type = entry.get('type')
            if entry_type not in ('user', 'assistant'):
                continue

            msg = entry.get('message', {})
            external_id = entry.get('uuid')
            role = msg.get('role', entry_type)
            content = msg.get('content', '')
            timestamp = entry.get('timestamp')

            # Skip if already logged
            if external_id and external_id in existing_messages:
                stats['skipped'] += 1
                continue

            # Insert message
            text_content = extract_text_content(content)
            cursor = conn.execute(
                "INSERT INTO messages (session_id, external_id, role, content, timestamp) VALUES (?, ?, ?, ?, ?)",
                (session_id, external_id, role, text_content, timestamp)
            )
            message_id = cursor.lastrowid
            stats['messages'] += 1

            # Extract and log tool calls from assistant messages
            if role == 'assistant':
                tool_calls = extract_tool_calls(content)
                for tc in tool_calls:
                    # Find the result
                    result = find_tool_result(transcript, tc['tool_use_id'])

                    cursor = conn.execute("""
                        INSERT INTO tool_calls (message_id, tool, input, output, success, error_message)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (
                        message_id,
                        tc['tool'],
                        tc['input'],
                        result['output'] if result else None,
                        result['success'] if result else 1,
                        result['error_message'] if result else None
                    ))
                    stats['tool_calls'] += 1

        conn.commit()

        # Update session ended_at
        conn.execute(
            "UPDATE sessions SET ended_at = CURRENT_TIMESTAMP, status = 'completed' WHERE id = ?",
            (session_id,)
        )
        conn.commit()

        log(f"Logged {stats['messages']} messages, {stats['tool_calls']} tool calls")
        stats['session_id'] = session_id
        stats['project_path'] = cwd
        return stats

    finally:
        conn.close()


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python history_logger.py <transcript_path>")
        sys.exit(1)

    path = sys.argv[1]
    stats = log_transcript(path, log_fn=print)
    print(f"Result: {stats}")
