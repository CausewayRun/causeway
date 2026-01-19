
import sys
import os
import asyncio
from pathlib import Path

# Fix path to include project root
root = Path(__file__).parent
sys.path.append(str(root))

from causeway.server import create_rule, RuleCreate, delete_rule
from causeway.db import init_db, get_connection

# Initialize DB
init_db()

# Create a semantic rule
rule_data = RuleCreate(
    type="semantic",
    description="Do not usage huge files",
    action="block",
    active=1,
    priority=1
)

print(f"Creating rule: {rule_data.description}")
try:
    result = create_rule(rule_data)
    rule_id = result["id"]
    print(f"Rule created with ID: {rule_id}")

    # Check if embedding exists
    conn = get_connection()
    cursor = conn.execute("SELECT * FROM rule_embeddings WHERE rule_id = ?", (rule_id,))
    row = cursor.fetchone()
    conn.close()

    if row:
        print("SUCCESS: Embedding exists.")
    else:
        print("FAILURE: Embedding does NOT exist.")

    # Clean up
    delete_rule(rule_id)
    print("Cleaned up rule.")

except Exception as e:
    print(f"Error: {e}")
