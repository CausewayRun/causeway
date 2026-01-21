"""Tests for FastAPI server endpoints."""
import os
import tempfile
import pytest
from unittest.mock import patch, MagicMock


@pytest.fixture(autouse=True)
def setup_db(tmp_path):
    """Initialize fresh database for each test."""
    test_db = str(tmp_path / 'test_server.db')
    os.environ['CAUSEWAY_DB'] = test_db

    from causeway.db import init_db
    init_db()
    yield
    # Cleanup
    if os.path.exists(test_db):
        try:
            os.remove(test_db)
        except OSError:
            pass


def get_connection():
    """Import and return get_connection from db module."""
    from causeway.db import get_connection as _get_connection
    return _get_connection()


@pytest.fixture
def client():
    """Create test client for FastAPI app."""
    from fastapi.testclient import TestClient
    from causeway.server import app
    return TestClient(app)


class TestRulesEndpoints:
    """Test /api/rules endpoints."""

    def test_list_rules_empty(self, client):
        """List rules returns empty array when no rules exist."""
        response = client.get("/api/rules")
        assert response.status_code == 200
        # May have default rules, so just check it's a list
        assert isinstance(response.json(), list)

    def test_create_rule(self, client):
        """Create rule returns new rule ID."""
        rule_data = {
            "type": "regex",
            "pattern": "^rm -rf",
            "description": "Block dangerous rm commands",
            "action": "block"
        }
        response = client.post("/api/rules", json=rule_data)
        assert response.status_code == 200
        data = response.json()
        assert "id" in data
        assert isinstance(data["id"], int)

    def test_get_rule(self, client):
        """Get rule returns rule details."""
        # Create a rule first
        rule_data = {
            "type": "regex",
            "pattern": "test-pattern",
            "description": "Test rule",
            "action": "warn"
        }
        create_response = client.post("/api/rules", json=rule_data)
        rule_id = create_response.json()["id"]

        # Get the rule
        response = client.get(f"/api/rules/{rule_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["description"] == "Test rule"
        assert data["pattern"] == "test-pattern"
        assert data["action"] == "warn"

    def test_get_rule_not_found(self, client):
        """Get nonexistent rule returns 404."""
        response = client.get("/api/rules/99999")
        assert response.status_code == 404

    def test_update_rule(self, client):
        """Update rule modifies rule fields."""
        # Create a rule
        rule_data = {
            "type": "regex",
            "description": "Original description",
            "action": "warn"
        }
        create_response = client.post("/api/rules", json=rule_data)
        rule_id = create_response.json()["id"]

        # Update the rule
        update_data = {"description": "Updated description", "action": "block"}
        response = client.put(f"/api/rules/{rule_id}", json=update_data)
        assert response.status_code == 200

        # Verify update
        get_response = client.get(f"/api/rules/{rule_id}")
        data = get_response.json()
        assert data["description"] == "Updated description"
        assert data["action"] == "block"

    def test_update_rule_not_found(self, client):
        """Update nonexistent rule returns 404."""
        update_data = {"description": "New description"}
        response = client.put("/api/rules/99999", json=update_data)
        assert response.status_code == 404

    @pytest.mark.skip(reason="Requires sqlite-vec extension which may not be available in test env")
    def test_delete_rule(self, client):
        """Delete rule removes rule from database.

        Note: This test requires sqlite-vec extension to be loaded.
        The delete_rule endpoint tries to delete from rule_embeddings which uses vec0.
        """
        # Create a rule
        rule_data = {"type": "regex", "description": "To delete", "action": "warn"}
        create_response = client.post("/api/rules", json=rule_data)
        rule_id = create_response.json()["id"]

        # Delete the rule
        response = client.delete(f"/api/rules/{rule_id}")
        assert response.status_code == 200

        # Verify deletion
        get_response = client.get(f"/api/rules/{rule_id}")
        assert get_response.status_code == 404

    def test_toggle_rule(self, client):
        """Toggle rule switches active status."""
        # Create a rule (active by default)
        rule_data = {"type": "regex", "description": "Toggle test", "action": "warn"}
        create_response = client.post("/api/rules", json=rule_data)
        rule_id = create_response.json()["id"]

        # Toggle off
        response = client.patch(f"/api/rules/{rule_id}/toggle")
        assert response.status_code == 200
        assert response.json()["active"] == 0

        # Toggle on
        response = client.patch(f"/api/rules/{rule_id}/toggle")
        assert response.status_code == 200
        assert response.json()["active"] == 1

    def test_toggle_rule_not_found(self, client):
        """Toggle nonexistent rule returns 404."""
        response = client.patch("/api/rules/99999/toggle")
        assert response.status_code == 404

    def test_rule_history(self, client):
        """Get rule history returns rule with source info."""
        # Create a rule
        rule_data = {"type": "regex", "description": "History test", "action": "warn"}
        create_response = client.post("/api/rules", json=rule_data)
        rule_id = create_response.json()["id"]

        # Get history
        response = client.get(f"/api/rules/{rule_id}/history")
        assert response.status_code == 200
        data = response.json()
        assert data["description"] == "History test"
        assert "triggers" in data


class TestSessionsEndpoints:
    """Test /api/sessions endpoints."""

    def test_list_sessions_empty(self, client):
        """List sessions returns empty array when no sessions."""
        response = client.get("/api/sessions")
        assert response.status_code == 200
        assert response.json() == []

    def test_list_sessions_with_data(self, client):
        """List sessions returns session data."""
        # Create a project and session
        conn = get_connection()
        cursor = conn.execute(
            "INSERT INTO projects (path, name) VALUES (?, ?)",
            ('/test/path', 'test-project')
        )
        project_id = cursor.lastrowid

        conn.execute(
            "INSERT INTO sessions (project_id, external_id, task) VALUES (?, ?, ?)",
            (project_id, 'session-123', 'Test task')
        )
        conn.commit()
        conn.close()

        response = client.get("/api/sessions")
        assert response.status_code == 200
        sessions = response.json()
        assert len(sessions) >= 1
        assert sessions[0]["task"] == "Test task"

    def test_get_session(self, client):
        """Get session returns session with messages."""
        # Create a project, session, and message
        conn = get_connection()
        cursor = conn.execute(
            "INSERT INTO projects (path, name) VALUES (?, ?)",
            ('/test/path', 'test-project')
        )
        project_id = cursor.lastrowid

        cursor = conn.execute(
            "INSERT INTO sessions (project_id, external_id, task) VALUES (?, ?, ?)",
            (project_id, 'session-123', 'Test task')
        )
        session_id = cursor.lastrowid

        conn.execute(
            "INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)",
            (session_id, 'user', 'Hello world')
        )
        conn.commit()
        conn.close()

        response = client.get(f"/api/sessions/{session_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["session"]["task"] == "Test task"
        assert len(data["messages"]) == 1
        assert data["messages"][0]["content"] == "Hello world"

    def test_get_session_not_found(self, client):
        """Get nonexistent session returns 404."""
        response = client.get("/api/sessions/99999")
        assert response.status_code == 404


class TestStatsEndpoint:
    """Test /api/stats endpoint."""

    def test_get_stats(self, client):
        """Get stats returns rule statistics."""
        # Create some rules
        conn = get_connection()
        conn.execute(
            "INSERT INTO rules (type, description, action, active) VALUES (?, ?, ?, ?)",
            ('regex', 'Block rule', 'block', 1)
        )
        conn.execute(
            "INSERT INTO rules (type, description, action, active) VALUES (?, ?, ?, ?)",
            ('regex', 'Warn rule', 'warn', 1)
        )
        conn.execute(
            "INSERT INTO rules (type, description, action, active, llm_review) VALUES (?, ?, ?, ?, ?)",
            ('regex', 'LLM rule', 'warn', 1, 1)
        )
        conn.execute(
            "INSERT INTO rules (type, description, action, active) VALUES (?, ?, ?, ?)",
            ('regex', 'Inactive rule', 'block', 0)
        )
        conn.commit()
        conn.close()

        response = client.get("/api/stats")
        assert response.status_code == 200
        stats = response.json()
        assert stats["total"] >= 4
        assert stats["active"] >= 3
        assert stats["block"] >= 1
        assert stats["warn"] >= 2
        assert stats["llm_review"] >= 1


class TestTracesEndpoints:
    """Test /api/traces endpoints."""

    def test_list_traces_empty(self, client):
        """List traces returns empty array when no traces."""
        response = client.get("/api/traces")
        assert response.status_code == 200
        assert response.json() == []

    def test_list_traces_with_data(self, client):
        """List traces returns trace data."""
        # Create a trace
        conn = get_connection()
        conn.execute("""
            INSERT INTO traces (hook_type, tool_name, tool_input, rules_checked,
                              rules_matched, matched_rule_ids, decision, reason, duration_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, ('pre', 'Bash', 'ls -la', 5, 1, '[1]', 'allow', 'No violations', 50))
        conn.commit()
        conn.close()

        response = client.get("/api/traces")
        assert response.status_code == 200
        traces = response.json()
        assert len(traces) >= 1
        assert traces[0]["tool_name"] == "Bash"
        assert traces[0]["decision"] == "allow"

    def test_clear_traces(self, client):
        """Clear traces removes all traces."""
        # Create a trace
        conn = get_connection()
        conn.execute("""
            INSERT INTO traces (hook_type, tool_name, tool_input, rules_checked,
                              rules_matched, matched_rule_ids, decision, reason, duration_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, ('pre', 'Bash', 'ls', 0, 0, '[]', 'allow', '', 10))
        conn.commit()
        conn.close()

        # Clear traces
        response = client.delete("/api/traces")
        assert response.status_code == 200

        # Verify cleared
        list_response = client.get("/api/traces")
        assert list_response.json() == []


class TestSettingsEndpoints:
    """Test /api/settings endpoints."""

    def test_get_settings_defaults(self, client):
        """Get settings returns defaults when no custom settings."""
        response = client.get("/api/settings")
        assert response.status_code == 200
        settings = response.json()
        assert "eval_model" in settings
        assert "learn_model" in settings

    def test_update_setting(self, client):
        """Update setting modifies setting value."""
        response = client.put("/api/settings/eval_model", json={"value": "custom-model"})
        assert response.status_code == 200

        # Verify update
        get_response = client.get("/api/settings")
        settings = get_response.json()
        assert settings["eval_model"] == "custom-model"

    def test_update_unknown_setting(self, client):
        """Update unknown setting returns error."""
        response = client.put("/api/settings/unknown_key", json={"value": "test"})
        assert response.status_code == 200
        data = response.json()
        assert "error" in data


class TestVersionEndpoint:
    """Test /api/version endpoint."""

    def test_get_version(self, client):
        """Get version returns version info."""
        with patch('causeway.server.check_for_updates') as mock_check:
            mock_check.return_value = {
                "current_version": "1.0.0",
                "update_available": False,
                "latest_version": "1.0.0",
                "on_edge": False,
                "release_url": None
            }

            response = client.get("/api/version")
            assert response.status_code == 200
            data = response.json()
            assert "version" in data
            assert "update_available" in data


class TestIndexEndpoint:
    """Test / endpoint."""

    def test_index_returns_html(self, client):
        """Index returns HTML dashboard."""
        response = client.get("/")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "causeway" in response.text
