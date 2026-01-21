# Causeway Tests

This directory contains unit and integration tests for the Causeway project.

## Test Structure

```
tests/
├── conftest.py              # Shared pytest fixtures
├── integration/             # Integration tests (real API calls)
│   ├── test_embeddings.py   # OpenAI embedding generation
│   ├── test_mcp_tools.py    # MCP tool handlers
│   ├── test_rule_checking.py # LLM-based rule evaluation
│   └── test_sqlite_vec.py   # sqlite-vec extension operations
├── test_check_rules.py      # Hook rule checking logic
├── test_cli.py              # CLI commands and config
├── test_db.py               # Database initialization
├── test_history_logger.py   # Transcript logging
├── test_learning_agent.py   # Learning agent models
├── test_mcp.py              # MCP server handlers
├── test_rule_agent.py       # Rule agent core logic
├── test_semantic_rules.py   # Semantic rule matching
└── test_server.py           # FastAPI endpoints
```

## Running Tests

### Prerequisites

```bash
# Install dev dependencies
uv sync --group dev
```

### Run All Tests

```bash
uv run pytest tests/ -v
```

### Run Unit Tests Only (Fast)

Unit tests use mocks and don't require API keys:

```bash
uv run pytest tests/ --ignore=tests/integration/ -v
```

### Run Integration Tests

Integration tests require `OPENAI_API_KEY` environment variable:

```bash
# Set API key (or use .env file in project root)
export OPENAI_API_KEY=your-key-here

# Run integration tests
uv run pytest tests/integration/ -v -m integration
```

### Run Specific Test Categories

```bash
# Skip slow tests
uv run pytest tests/ -v -m "not slow"

# Skip integration tests
uv run pytest tests/ -v -m "not integration"

# Run only async tests
uv run pytest tests/ -v -k "async"
```

## Test Categories

### Unit Tests

Fast tests that mock external dependencies:

- **test_db.py** - Database schema and table creation
- **test_cli.py** - CLI commands, config loading/saving, validation
- **test_check_rules.py** - Rule ID extraction, output formatting, trace logging
- **test_history_logger.py** - Transcript extraction and logging
- **test_learning_agent.py** - RuleChange/LearningOutput models, transcript formatting
- **test_mcp.py** - MCP tool handlers with mocked embeddings
- **test_rule_agent.py** - Regex rule matching, pattern arrays
- **test_semantic_rules.py** - Embedding functions, LLM review logic
- **test_server.py** - FastAPI REST endpoints

### Integration Tests

Tests that use real dependencies (API calls, sqlite-vec):

- **test_embeddings.py** - Real OpenAI `text-embedding-3-small` calls
  - Vector generation with 384 dimensions
  - Embedding storage in sqlite-vec
  - Semantic similarity search

- **test_rule_checking.py** - Real LLM-based rule evaluation
  - Semantic rules blocking violations
  - Regex rules with pattern matching
  - LLM review flag handling

- **test_sqlite_vec.py** - Real sqlite-vec extension
  - Extension loading verification
  - Vector distance search
  - Embedding CRUD operations

- **test_mcp_tools.py** - Real MCP tool execution
  - Rule creation with embeddings
  - Rule search with semantic matching
  - CRUD operations on rules

## Fixtures

Shared fixtures in `conftest.py`:

| Fixture | Scope | Description |
|---------|-------|-------------|
| `project_root` | session | Path to project root directory |
| `test_db` | function | Temporary database with sqlite-vec |
| `db_connection` | function | Database connection for test |
| `sample_rule` | function | Pre-created regex rule |
| `sample_semantic_rule` | function | Pre-created semantic rule |
| `sample_session` | function | Pre-created project and session |

## Markers

Custom pytest markers:

- `@pytest.mark.integration` - Requires API keys and makes real calls
- `@pytest.mark.slow` - Long-running tests
- `@pytest.mark.asyncio` - Async test functions (auto-detected)

## Environment Variables

| Variable | Required For | Description |
|----------|--------------|-------------|
| `OPENAI_API_KEY` | Integration tests | OpenAI API key for embeddings and LLM |
| `CAUSEWAY_DB` | Auto-set by fixtures | Database path (set automatically in tests) |

## Writing New Tests

### Unit Test Example

```python
def test_my_feature(test_db, db_connection):
    """Test description."""
    # test_db fixture creates isolated database
    # db_connection provides sqlite connection

    db_connection.execute("INSERT INTO rules ...")
    db_connection.commit()

    # Test your code
    from causeway.my_module import my_function
    result = my_function()

    assert result == expected
```

### Integration Test Example

```python
import pytest
import os

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get('OPENAI_API_KEY'),
        reason="OPENAI_API_KEY not set"
    )
]

class TestMyIntegration:
    def test_real_api_call(self, test_db):
        """Test with real API."""
        from causeway.rule_agent import generate_embedding

        embedding = generate_embedding("test text")

        assert len(embedding) == 384
```

## Troubleshooting

### MCP Import Errors

The MCP module uses graceful degradation. If MCP isn't installed, tests using MCP will be skipped automatically.

### sqlite-vec Errors

Ensure sqlite-vec is installed: `uv sync`. The extension is loaded automatically by `get_connection()`.

### Database Locking

Each test uses an isolated temporary database via the `test_db` fixture. If you see locking errors, ensure you're using the fixtures correctly.
