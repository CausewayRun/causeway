
import sys
import os
import unittest
from unittest.mock import MagicMock, patch

# Add project root to path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(project_root)
# Also add causeway subdir to path to support the fallback imports in server.py
sys.path.append(os.path.join(project_root, 'causeway'))

import traceback

# Mock missing AI dependencies
sys.modules['pydantic_ai'] = MagicMock()
sys.modules['openai'] = MagicMock()
# pydantic should be real, don't mock it or fastapi breaks
# sys.modules['pydantic'] = MagicMock()

# Mock sqlite_vec which is missing in env but used in db.py
sys.modules['sqlite_vec'] = MagicMock()
# Also mock numpy as it's used in serialize_vector and might be missing or optional
sys.modules['numpy'] = MagicMock()

# We need to mock serialize_vector in db because we mocked numpy
import causeway.db
def mock_serialize(vec):
    return b'dummy_bytes'
causeway.db.serialize_vector = mock_serialize

# Mock .env loading to avoid errors
with patch('dotenv.load_dotenv'):
    try:
        from causeway.server import create_rule, RuleCreate, update_rule, RuleUpdate, get_db
        from causeway.db import init_db
    except ImportError:
        with open('error.log', 'w') as f:
            traceback.print_exc(file=f)
        raise


def init_test_db(db_path):
    conn = sqlite3.connect(str(db_path))
    # Create tables
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS rules (
            id INTEGER PRIMARY KEY,
            type TEXT DEFAULT 'regex',
            pattern TEXT,
            description TEXT NOT NULL,
            tool TEXT,
            action TEXT DEFAULT 'block',
            active INTEGER DEFAULT 1,
            priority INTEGER DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            problem TEXT,
            solution TEXT,
            rule_set_id INTEGER,
            source_message_id INTEGER,
            source_session_id INTEGER,
            color TEXT,
            metadata TEXT,
            patterns TEXT,
            llm_review INTEGER DEFAULT 0,
            prompt TEXT,
            hard INTEGER DEFAULT 0
        );

        -- Standard table instead of virtual vec0/vec1
        CREATE TABLE IF NOT EXISTS rule_embeddings (
            rule_id INTEGER PRIMARY KEY,
            embedding BLOB
        );
        
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
    """)
    conn.close()

class TestSemanticRules(unittest.TestCase):
    def setUp(self):
        # Create temp dir
        self.test_dir = tempfile.mkdtemp()
        self.db_path = Path(self.test_dir) / "test.db"
        # Initialize DB with custom schema (no vector extension)
        init_test_db(self.db_path)
        # Patch get_db to use our temp DB
        self.db_patcher = patch('causeway.server.get_db_path', return_value=self.db_path)
        self.db_patcher.start()
        
        # Patch db.get_db_path to prevent rule_agent from picking up default path
        self.db_path_patcher = patch('causeway.db.get_db_path', return_value=self.db_path)
        self.db_path_patcher.start()

        # Patch get_connection to avoid extension loading issues entirely
        def simple_get_connection(db_path=None):
            path = db_path or self.db_path
            conn = sqlite3.connect(str(path))
            conn.row_factory = sqlite3.Row
            return conn
        
        self.conn_patcher = patch('causeway.db.get_connection', side_effect=simple_get_connection)
        self.conn_patcher.start()
        
    def tearDown(self):
        self.conn_patcher.stop()
        self.db_path_patcher.stop()
        self.db_patcher.stop()
        shutil.rmtree(self.test_dir)

    @patch('causeway.rule_agent.generate_embedding')
    def test_embedding_generation_on_create(self, mock_gen_embedding):
        # Mock embedding return
        mock_gen_embedding.return_value = [0.1] * 384
        
        # Create rule
        rule = RuleCreate(
            type="semantic",
            description="Test Semantic Rule",
            action="block"
        )
        
        print("Creating rule...")
        result = create_rule(rule)
        rule_id = result['id']
        
        # Verify call
        mock_gen_embedding.assert_called_with("Test Semantic Rule")
        
        # Verify DB
        conn = get_db()
        row = conn.execute("SELECT * FROM rule_embeddings WHERE rule_id = ?", (rule_id,)).fetchone()
        conn.close()
        
        self.assertIsNotNone(row, "Embedding should be created")
        print("SUCCESS: Embedding created for new rule")

    @patch('causeway.rule_agent.generate_embedding')
    def test_embedding_update_on_edit(self, mock_gen_embedding):
        # Mock embedding return
        mock_gen_embedding.return_value = [0.2] * 384
        
        # Manually insert initial rule matching what server.py expects
        conn = get_db()
        cursor = conn.execute("""
            INSERT INTO rules (type, description, action, active, priority, llm_review) 
            VALUES ('semantic', 'Initial Desc', 'block', 1, 0, 0)
        """)
        rule_id = cursor.lastrowid
        conn.commit()
        conn.close()

        print(f"Updating rule {rule_id}...")
        
        # Update rule description
        update = RuleUpdate(description="Updated Description")
        update_rule(rule_id, update)
        
        # Verify call
        mock_gen_embedding.assert_called_with("Updated Description")
        
        # Verify DB (checking if any embedding exists is enough proof update_rule_embedding was called)
        conn = get_db()
        row = conn.execute("SELECT * FROM rule_embeddings WHERE rule_id = ?", (rule_id,)).fetchone()
        conn.close()
        
        self.assertIsNotNone(row, "Embedding should be created/updated")
        print("SUCCESS: Embedding updated for edited rule")

if __name__ == '__main__':
    unittest.main()
