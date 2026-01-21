"""Shared pytest fixtures for causeway tests.

This conftest.py provides fixtures for both unit and integration tests.
It loads .env from the project root BEFORE importing causeway modules.
"""
import os
import pytest
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root BEFORE any causeway imports
PROJECT_ROOT = Path(__file__).parent.parent
load_dotenv(PROJECT_ROOT / ".env")


@pytest.fixture(scope="session")
def project_root():
    """Return the project root directory."""
    return PROJECT_ROOT


@pytest.fixture
def test_db(tmp_path):
    """Create isolated test database with sqlite-vec loaded.

    This fixture:
    1. Creates a temporary database file
    2. Sets CAUSEWAY_DB environment variable
    3. Initializes the database with all tables
    4. Yields the database path
    5. Cleans up after the test
    """
    db_path = tmp_path / "test.db"
    os.environ['CAUSEWAY_DB'] = str(db_path)

    from causeway.db import init_db
    init_db()

    yield db_path

    # Cleanup
    if db_path.exists():
        try:
            db_path.unlink()
        except OSError:
            pass


@pytest.fixture
def db_connection(test_db):
    """Get database connection with sqlite-vec loaded.

    This fixture provides a connection to the test database.
    The connection is automatically closed after the test.
    """
    from causeway.db import get_connection
    conn = get_connection()
    yield conn
    conn.close()


@pytest.fixture
def sample_rule(db_connection):
    """Create a sample rule in the database."""
    cursor = db_connection.execute(
        """INSERT INTO rules (type, description, action, active)
           VALUES (?, ?, ?, ?)""",
        ('regex', 'Test rule - block rm -rf', 'block', 1)
    )
    rule_id = cursor.lastrowid
    db_connection.commit()
    return {
        'id': rule_id,
        'type': 'regex',
        'description': 'Test rule - block rm -rf',
        'action': 'block',
        'active': 1
    }


@pytest.fixture
def sample_semantic_rule(db_connection):
    """Create a sample semantic rule in the database."""
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
    return {
        'id': rule_id,
        'type': 'semantic',
        'description': 'Always use uv instead of pip for package management',
        'problem': 'pip is slower and has dependency resolution issues',
        'solution': 'Use uv for faster, more reliable package management',
        'action': 'block',
        'active': 1
    }


@pytest.fixture
def sample_session(db_connection):
    """Create a sample project and session in the database."""
    # Create project
    cursor = db_connection.execute(
        "INSERT INTO projects (path, name) VALUES (?, ?)",
        ('/test/project', 'test-project')
    )
    project_id = cursor.lastrowid

    # Create session
    cursor = db_connection.execute(
        "INSERT INTO sessions (project_id, external_id, task) VALUES (?, ?, ?)",
        (project_id, 'test-session-123', 'Test task')
    )
    session_id = cursor.lastrowid
    db_connection.commit()

    return {
        'project_id': project_id,
        'session_id': session_id,
        'external_id': 'test-session-123',
        'task': 'Test task'
    }


# Markers for test categories
def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line(
        "markers", "integration: marks tests as integration tests (require API keys)"
    )
    config.addinivalue_line(
        "markers", "slow: marks tests as slow (deselect with '-m not slow')"
    )
