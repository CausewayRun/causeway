"""Integration tests for sqlite-vec extension.

These tests verify that sqlite-vec is properly loaded and functional.
Run with: uv run pytest tests/integration/test_sqlite_vec.py -v -m integration
"""
import pytest
import struct

pytestmark = pytest.mark.integration


class TestSqliteVecLoading:
    """Test sqlite-vec extension loading."""

    def test_vec_extension_loaded(self, test_db, db_connection):
        """Verify sqlite-vec extension is loaded and vec0 table type exists."""
        # Check if vec0 virtual table type is available
        # This query would fail if sqlite-vec isn't loaded
        try:
            db_connection.execute("SELECT vec_version()")
            vec_available = True
        except Exception:
            vec_available = False

        assert vec_available, "sqlite-vec extension not loaded"

    def test_rule_embeddings_table_is_vec0(self, test_db, db_connection):
        """Verify rule_embeddings uses vec0 virtual table."""
        # Check table schema
        row = db_connection.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'rule_embeddings'"
        ).fetchone()

        assert row is not None
        assert 'vec0' in row['sql'].lower(), \
            f"Expected vec0 table, got: {row['sql']}"

    def test_vec_distance_search(self, test_db, db_connection):
        """Test vector distance search functionality."""
        from causeway.db import serialize_vector

        # Insert a rule
        cursor = db_connection.execute(
            "INSERT INTO rules (type, description, action, active) VALUES (?, ?, ?, ?)",
            ('semantic', 'Test rule for vec search', 'warn', 1)
        )
        rule_id = cursor.lastrowid
        db_connection.commit()

        # Create a test embedding (384 dimensions)
        test_embedding = [0.1] * 384
        embedding_bytes = serialize_vector(test_embedding)

        # Insert embedding
        db_connection.execute(
            "INSERT INTO rule_embeddings (rule_id, embedding) VALUES (?, ?)",
            (rule_id, embedding_bytes)
        )
        db_connection.commit()

        # Search with similar embedding
        query_embedding = [0.1] * 384
        query_bytes = serialize_vector(query_embedding)

        results = db_connection.execute("""
            SELECT rule_id, distance
            FROM rule_embeddings
            WHERE embedding MATCH ? AND k = 5
            ORDER BY distance
        """, (query_bytes,)).fetchall()

        assert len(results) >= 1
        assert results[0]['rule_id'] == rule_id
        # Distance should be very small since embeddings are identical
        assert results[0]['distance'] < 0.01


class TestVectorSerialization:
    """Test vector serialization for sqlite-vec."""

    def test_serialize_vector_format(self, test_db):
        """Test that serialize_vector produces correct binary format."""
        from causeway.db import serialize_vector

        vector = [1.0, 2.0, 3.0]
        serialized = serialize_vector(vector)

        # Should be binary data
        assert isinstance(serialized, bytes)

        # Should be 4 bytes per float32
        assert len(serialized) == len(vector) * 4

        # Verify values can be deserialized
        unpacked = struct.unpack(f'{len(vector)}f', serialized)
        for original, restored in zip(vector, unpacked):
            assert abs(original - restored) < 0.0001

    def test_serialize_384_dim_vector(self, test_db):
        """Test serialization of 384-dimensional vectors (OpenAI embedding size)."""
        from causeway.db import serialize_vector

        vector = [float(i) / 384 for i in range(384)]
        serialized = serialize_vector(vector)

        assert len(serialized) == 384 * 4  # 384 floats * 4 bytes each


class TestEmbeddingCRUD:
    """Test embedding create/read/update/delete operations."""

    def test_create_and_retrieve_embedding(self, test_db, db_connection):
        """Test creating and retrieving an embedding."""
        from causeway.db import serialize_vector

        # Create rule
        cursor = db_connection.execute(
            "INSERT INTO rules (type, description, action, active) VALUES (?, ?, ?, ?)",
            ('semantic', 'CRUD test rule', 'block', 1)
        )
        rule_id = cursor.lastrowid
        db_connection.commit()

        # Create embedding
        embedding = [0.5] * 384
        embedding_bytes = serialize_vector(embedding)
        db_connection.execute(
            "INSERT INTO rule_embeddings (rule_id, embedding) VALUES (?, ?)",
            (rule_id, embedding_bytes)
        )
        db_connection.commit()

        # Verify retrieval
        row = db_connection.execute(
            "SELECT rule_id FROM rule_embeddings WHERE rule_id = ?",
            (rule_id,)
        ).fetchone()

        assert row is not None
        assert row['rule_id'] == rule_id

    def test_delete_embedding_cascade(self, test_db, db_connection):
        """Test that deleting a rule doesn't leave orphan embeddings."""
        from causeway.db import serialize_vector

        # Create rule with embedding
        cursor = db_connection.execute(
            "INSERT INTO rules (type, description, action, active) VALUES (?, ?, ?, ?)",
            ('semantic', 'Delete cascade test', 'warn', 1)
        )
        rule_id = cursor.lastrowid
        db_connection.commit()

        embedding = [0.3] * 384
        embedding_bytes = serialize_vector(embedding)
        db_connection.execute(
            "INSERT INTO rule_embeddings (rule_id, embedding) VALUES (?, ?)",
            (rule_id, embedding_bytes)
        )
        db_connection.commit()

        # Delete embedding first (as done in mcp.py delete_rule)
        db_connection.execute(
            "DELETE FROM rule_embeddings WHERE rule_id = ?",
            (rule_id,)
        )
        # Then delete rule
        db_connection.execute(
            "DELETE FROM rules WHERE id = ?",
            (rule_id,)
        )
        db_connection.commit()

        # Verify both are gone
        rule_row = db_connection.execute(
            "SELECT id FROM rules WHERE id = ?",
            (rule_id,)
        ).fetchone()
        emb_row = db_connection.execute(
            "SELECT rule_id FROM rule_embeddings WHERE rule_id = ?",
            (rule_id,)
        ).fetchone()

        assert rule_row is None
        assert emb_row is None
