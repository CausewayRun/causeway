
import sys
import os
import unittest
from unittest.mock import MagicMock, patch

# Add project root to path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(project_root)
sys.path.append(os.path.join(project_root, 'causeway'))

# Mock EVERYTHING that is not needed for the core logic
sys.modules['pydantic_ai'] = MagicMock()
sys.modules['openai'] = MagicMock()
sys.modules['sqlite_vec'] = MagicMock()
sys.modules['numpy'] = MagicMock()
sys.modules['fastapi'] = MagicMock()
sys.modules['pydantic'] = MagicMock()

import sqlite3
from pathlib import Path

# Use a custom simple connection to bypass everything
def get_mock_conn(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

# We will manually test the update_rule_embedding function from rule_agent.py
# But we need to mock generate_embedding and serialize_vector

@patch('causeway.rule_agent.generate_embedding')
@patch('causeway.rule_agent.serialize_vector')
@patch('causeway.rule_agent.get_connection')
def test_logic(mock_get_conn, mock_serialize, mock_gen_embedding):
    from rule_agent import update_rule_embedding
    
    # Setup
    db_file = "temp_test.db"
    if os.path.exists(db_file): os.remove(db_file)
    conn = sqlite3.connect(db_file)
    conn.execute("CREATE TABLE rule_embeddings (rule_id INTEGER PRIMARY KEY, embedding BLOB)")
    conn.close()
    
    mock_get_conn.side_effect = lambda: get_mock_conn(db_file)
    mock_gen_embedding.return_value = [0.1] * 384
    mock_serialize.return_value = b"bytes"
    
    # Test
    print("Testing update_rule_embedding...")
    update_rule_embedding(1, "test description")
    
    # Verify
    conn = sqlite3.connect(db_file)
    row = conn.execute("SELECT * FROM rule_embeddings WHERE rule_id = 1").fetchone()
    conn.close()
    
    if row and row[1] == b"bytes":
        print("CORE LOGIC VERIFIED: Embedding saved to DB.")
    else:
        print(f"FAILED: Row is {row}")
    
    if os.path.exists(db_file): os.remove(db_file)

if __name__ == "__main__":
    test_logic()
