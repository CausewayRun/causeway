"""Tests for CLI commands."""
import os
import json
import tempfile
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

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


@pytest.fixture
def temp_config_dir(tmp_path):
    """Create a temporary config directory."""
    return tmp_path


class TestConfigFunctions:
    """Test config loading and saving."""

    def test_load_config_empty(self, temp_config_dir):
        """Load config returns empty dict when no file exists."""
        from causeway.cli import load_config, CAUSEWAY_DIR

        with patch.object(Path, 'exists', return_value=False):
            # Patch CAUSEWAY_DIR to use temp directory
            with patch('causeway.cli.CAUSEWAY_DIR', temp_config_dir):
                config = load_config()
                assert config == {}

    def test_load_config_with_data(self, temp_config_dir):
        """Load config parses key=value pairs."""
        env_file = temp_config_dir / ".env"
        env_file.write_text("KEY1=value1\nKEY2=value2\n# comment\nKEY3=value with spaces\n")

        with patch('causeway.cli.CAUSEWAY_DIR', temp_config_dir):
            from causeway.cli import load_config
            config = load_config()
            assert config.get('KEY1') == 'value1'
            assert config.get('KEY2') == 'value2'
            assert config.get('KEY3') == 'value with spaces'

    def test_save_config(self, temp_config_dir):
        """Save config writes key=value pairs."""
        with patch('causeway.cli.CAUSEWAY_DIR', temp_config_dir):
            from causeway.cli import save_config, load_config

            config = {'KEY1': 'value1', 'KEY2': 'value2'}
            save_config(config)

            loaded = load_config()
            assert loaded.get('KEY1') == 'value1'
            assert loaded.get('KEY2') == 'value2'

    def test_get_install_id_creates_new(self, temp_config_dir):
        """Get install ID creates new UUID if none exists."""
        with patch('causeway.cli.CAUSEWAY_DIR', temp_config_dir):
            from causeway.cli import get_install_id

            install_id = get_install_id()
            assert install_id is not None
            assert len(install_id) == 36  # UUID format

    def test_get_install_id_returns_existing(self, temp_config_dir):
        """Get install ID returns existing ID if present."""
        existing_id = "test-install-id-123"
        id_file = temp_config_dir / ".install_id"
        id_file.write_text(existing_id)

        with patch('causeway.cli.CAUSEWAY_DIR', temp_config_dir):
            from causeway.cli import get_install_id

            install_id = get_install_id()
            assert install_id == existing_id


class TestValidation:
    """Test validation functions."""

    def test_validate_api_key_valid(self):
        """Valid API key returns True."""
        from causeway.cli import validate_api_key

        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = MagicMock()
            valid, msg = validate_api_key('openai', 'sk-valid-key')
            assert valid is True
            assert msg == "Valid"

    def test_validate_api_key_invalid(self):
        """Invalid API key returns False with message."""
        from causeway.cli import validate_api_key
        import urllib.error

        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.side_effect = urllib.error.HTTPError(
                url=None, code=401, msg='Unauthorized', hdrs=None, fp=None
            )
            valid, msg = validate_api_key('openai', 'invalid-key')
            assert valid is False
            assert "Invalid API key" in msg

    def test_validate_api_key_forbidden(self):
        """Forbidden response returns False with access denied."""
        from causeway.cli import validate_api_key
        import urllib.error

        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.side_effect = urllib.error.HTTPError(
                url=None, code=403, msg='Forbidden', hdrs=None, fp=None
            )
            valid, msg = validate_api_key('openai', 'forbidden-key')
            assert valid is False
            assert "Access denied" in msg


class TestCmdList:
    """Test the list command."""

    def test_list_no_rules(self, capsys):
        """List shows message when no rules exist."""
        from causeway.cli import cmd_list

        cmd_list()
        captured = capsys.readouterr()
        assert "No active rules" in captured.out

    def test_list_with_rules(self, capsys):
        """List shows active rules."""
        # Add a rule
        conn = get_connection()
        conn.execute(
            "INSERT INTO rules (type, description, action) VALUES (?, ?, ?)",
            ('regex', 'Test rule description', 'block')
        )
        conn.commit()
        conn.close()

        from causeway.cli import cmd_list
        cmd_list()

        captured = capsys.readouterr()
        assert "Test rule description" in captured.out
        assert "block" in captured.out


class TestCmdRulesets:
    """Test the rulesets command."""

    def test_rulesets_lists_available(self, capsys):
        """Rulesets command lists available rulesets."""
        from causeway.cli import cmd_rulesets

        cmd_rulesets()
        captured = capsys.readouterr()
        # Should list at least one ruleset
        assert "rules" in captured.out.lower() or len(captured.out) > 0


class TestCmdAdd:
    """Test the add command."""

    def test_add_unknown_ruleset(self, capsys):
        """Add command exits with error for unknown ruleset."""
        from causeway.cli import cmd_add

        with pytest.raises(SystemExit) as exc_info:
            cmd_add('nonexistent-ruleset')

        assert exc_info.value.code == 1


class TestEmailValidation:
    """Test email regex validation."""

    def test_valid_emails(self):
        """Valid email addresses match regex."""
        from causeway.cli import EMAIL_REGEX

        valid_emails = [
            "test@example.com",
            "user.name@domain.org",
            "user+tag@example.co.uk",
        ]

        for email in valid_emails:
            assert EMAIL_REGEX.match(email) is not None, f"{email} should be valid"

    def test_invalid_emails(self):
        """Invalid email addresses don't match regex."""
        from causeway.cli import EMAIL_REGEX

        invalid_emails = [
            "not-an-email",
            "@missing-user.com",
            "missing-domain@",
            "spaces in@email.com",
        ]

        for email in invalid_emails:
            assert EMAIL_REGEX.match(email) is None, f"{email} should be invalid"


class TestCmdInit:
    """Test the init command."""

    def test_init_creates_database(self, tmp_path, capsys):
        """Init command creates database in project directory."""
        from causeway.cli import cmd_init

        with patch('causeway.cli.ORIG_CWD', str(tmp_path)):
            cmd_init()

        db_path = tmp_path / ".causeway" / "brain.db"
        assert db_path.exists()

        captured = capsys.readouterr()
        assert "Initialized" in captured.out
