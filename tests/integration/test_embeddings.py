"""Integration tests for real OpenAI embeddings.

These tests make actual API calls to OpenAI.
Run with: uv run pytest tests/integration/test_embeddings.py -v -m integration
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


class TestRealEmbeddings:
    """Test real OpenAI embedding generation."""

    def test_generate_embedding_returns_vector(self, test_db):
        """Test generating actual embeddings with OpenAI API."""
        from causeway.rule_agent import generate_embedding

        embedding = generate_embedding("Use uv instead of pip")

        assert len(embedding) == 384
        assert all(isinstance(x, float) for x in embedding)

    def test_embedding_dimension_consistency(self, test_db):
        """Embeddings should always be 384 dimensions."""
        from causeway.rule_agent import generate_embedding

        texts = [
            "Short",
            "A medium length text about programming",
            "A much longer text that contains many words and discusses various topics including software development, testing practices, and code quality"
        ]

        for text in texts:
            embedding = generate_embedding(text)
            assert len(embedding) == 384, f"Expected 384 dimensions for: {text[:20]}..."

    def test_similar_texts_have_similar_embeddings(self, test_db):
        """Similar texts should produce similar embeddings."""
        import numpy as np
        from causeway.rule_agent import generate_embedding

        # Very similar texts
        emb1 = generate_embedding("Install packages with pip")
        emb2 = generate_embedding("Use pip to install packages")

        # Different text
        emb3 = generate_embedding("The quick brown fox jumps")

        # Calculate cosine similarities
        def cosine_similarity(a, b):
            return np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))

        sim_similar = cosine_similarity(emb1, emb2)
        sim_different = cosine_similarity(emb1, emb3)

        # Similar texts should have higher similarity
        assert sim_similar > sim_different, \
            f"Similar texts similarity ({sim_similar:.3f}) should be > different texts ({sim_different:.3f})"


class TestRealEmbeddingStorage:
    """Test storing and retrieving embeddings from sqlite-vec."""

    def test_ensure_rule_embedding_creates_entry(self, test_db, db_connection):
        """Test that ensure_rule_embedding stores real embedding in database."""
        from causeway.rule_agent import ensure_rule_embedding

        # Create a rule first
        cursor = db_connection.execute(
            """INSERT INTO rules (type, description, action, active)
               VALUES (?, ?, ?, ?)""",
            ('semantic', 'Always use uv instead of pip', 'block', 1)
        )
        rule_id = cursor.lastrowid
        db_connection.commit()

        # Generate and store embedding
        ensure_rule_embedding(rule_id, "Always use uv instead of pip")

        # Verify embedding exists
        row = db_connection.execute(
            "SELECT rule_id FROM rule_embeddings WHERE rule_id = ?",
            (rule_id,)
        ).fetchone()

        assert row is not None
        assert row['rule_id'] == rule_id

    def test_find_semantic_rules_with_real_embeddings(self, test_db, db_connection):
        """Test semantic rule search with real embeddings."""
        from causeway.rule_agent import ensure_rule_embedding, find_semantic_rules

        # Create rules with embeddings
        rules = [
            ('semantic', 'Always use uv instead of pip for package management', 'block'),
            ('semantic', 'Never use rm -rf on important directories', 'block'),
            ('semantic', 'Use TypeScript instead of JavaScript when possible', 'warn'),
        ]

        for rule_type, description, action in rules:
            cursor = db_connection.execute(
                """INSERT INTO rules (type, description, action, active)
                   VALUES (?, ?, ?, ?)""",
                (rule_type, description, action, 1)
            )
            rule_id = cursor.lastrowid
            db_connection.commit()
            ensure_rule_embedding(rule_id, description)

        # Search for pip-related rules
        results = find_semantic_rules('Bash', 'pip install requests')

        # Should find the pip/uv rule as most relevant
        assert len(results) > 0
        # The first result should be related to pip/uv
        found_pip_rule = any('uv' in r['description'].lower() or 'pip' in r['description'].lower()
                            for r in results)
        assert found_pip_rule, f"Expected to find pip/uv rule, got: {results}"
