"""Rule checking agent - regex patterns with optional LLM review."""
import re
import json
from pathlib import Path
from dataclasses import dataclass
from dotenv import load_dotenv
from pydantic import BaseModel
from pydantic_ai import Agent
from openai import OpenAI

load_dotenv(Path(__file__).parent / ".env")

from .db import get_connection, init_db, serialize_vector

_openai_client = None


def get_openai_client() -> OpenAI:
    global _openai_client
    if _openai_client is None:
        _openai_client = OpenAI()
    return _openai_client


def generate_embedding(text: str) -> list[float]:
    client = get_openai_client()
    response = client.embeddings.create(
        model="text-embedding-3-small",
        input=text,
        dimensions=384
    )
    return response.data[0].embedding


class RuleDecision(BaseModel):
    """Structured output from rule checking."""
    approved: bool
    action: str  # "block", "warn", or "allow"
    comment: str


@dataclass
class RuleContext:
    tool_name: str
    tool_input: str
    relevant_rules: list[dict]


_rule_agent = None

SYSTEM_PROMPT = """You are a rule enforcer. Only flag ACTUAL VIOLATIONS.

CRITICAL: If the input already complies with a rule, return action="allow".
Do NOT suggest improvements or stylistic changes. Only flag violations.

Example: Rule "Use uv run" + Input "uv run uvicorn ..." → ALLOW (already uses uv run!)

Rules are HARD or SOFT:
- HARD: Security rules. MUST enforce, no exceptions.
- SOFT: Preferences. Can be overridden with justification.

OVERRIDE: If justification starts with "OVERRIDE:" + valid reason → allow SOFT rules.
HARD rules cannot be overridden.

Only return action="block" or "warn" if input ACTUALLY VIOLATES the rule.
If compliant or irrelevant → action="allow"."""


def get_setting(key: str, default: str) -> str:
    """Get a setting from DB or return default."""
    try:
        conn = get_connection()
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        conn.close()
        return row['value'] if row else default
    except Exception:
        return default


def get_rule_agent() -> Agent:
    global _rule_agent
    # Always recreate to pick up settings changes
    model = get_setting('eval_model', 'openai:gpt-4o-mini')  # OVERRIDE: user explicitly requested gpt-4o-mini
    prompt = get_setting('eval_prompt', SYSTEM_PROMPT)
    _rule_agent = Agent(
        model,
        output_type=RuleDecision,
        system_prompt=prompt,
        deps_type=RuleContext,
    )
    return _rule_agent


def matches_patterns(tool_input: str, patterns_json: str | None) -> bool:
    """Check if tool input matches any regex pattern in the JSON array."""
    if not patterns_json:
        return False

    try:
        patterns = json.loads(patterns_json)
        if not isinstance(patterns, list):
            patterns = [patterns]

        for pattern in patterns:
            if re.search(pattern, tool_input, re.IGNORECASE):
                return True
        return False
    except (json.JSONDecodeError, re.error):
        return False


def check_regex_rules(tool_name: str, tool_input: str) -> tuple[bool, str | None, str | None, list[dict]]:
    """Check tool input against regex rules.

    Returns (passed, rejection_reason, action, rules_needing_llm_review).
    Collects ALL matching rules, not just first.
    """
    conn = get_connection()
    llm_review_needed = []
    matched_rules = []  # Collect all matched rules

    try:
        cursor = conn.execute("""
            SELECT id, pattern, patterns, description, action, llm_review, prompt, solution
            FROM rules
            WHERE type = 'regex'
            AND active = 1
            AND (tool IS NULL OR tool = ?)
            ORDER BY priority DESC
        """, (tool_name,))

        for row in cursor.fetchall():
            # Check single pattern (legacy) or patterns array
            matched = False

            if row['pattern'] and re.search(row['pattern'], tool_input):
                matched = True
            elif matches_patterns(tool_input, row['patterns']):
                matched = True

            if not matched:
                continue

            # Pattern matched - check if LLM review needed
            if row['llm_review']:
                llm_review_needed.append({
                    'id': row['id'],
                    'description': row['description'],
                    'action': row['action'],
                    'prompt': row['prompt'],
                    'tool_input': tool_input
                })
            else:
                # Collect matched rule
                matched_rules.append({
                    'id': row['id'],
                    'description': row['description'],
                    'action': row['action'],
                    'solution': row['solution']
                })

        # Build combined response from all matched rules
        if matched_rules:
            blocks = [r for r in matched_rules if r['action'] == 'block']
            warns = [r for r in matched_rules if r['action'] == 'warn']

            messages = []
            for r in blocks:
                msg = f"[BLOCK #{r['id']}] {r['description']}"
                if r['solution']:
                    msg += f" → {r['solution']}"
                messages.append(msg)
            for r in warns:
                msg = f"[WARN #{r['id']}] {r['description']}"
                if r['solution']:
                    msg += f" → {r['solution']}"
                messages.append(msg)

            # If any blocks, action=block. If only warns, action=warn.
            action = "block" if blocks else "warn"
            return False, "\n".join(messages), action, llm_review_needed

        return True, None, None, llm_review_needed
    finally:
        conn.close()


async def check_llm_review(rules: list[dict], tool_name: str, tool_input: str) -> RuleDecision:
    """Have LLM review matched rules to decide if action should be taken."""
    if not rules:
        return RuleDecision(approved=True, action="allow", comment="No review needed")

    # Limit to 5 most relevant rules
    rules = rules[:5]
    rules_text = []
    for r in rules:
        rule_str = f"Rule #{r['id']} ({r['action']}): {r['description'][:100]}"
        if r.get('prompt'):
            rule_str += f"\n  Check: {r['prompt'][:100]}"
        rules_text.append(rule_str)

    agent = get_rule_agent()
    result = await agent.run(f"""Tool: {tool_name}
Input:
```
{tool_input[:1000]}
```

Rules to evaluate:
{chr(10).join(rules_text)}

For each rule, does this input violate it?""")

    return result.output


def find_semantic_rules(tool_name: str, tool_input: str, top_k: int = 5) -> list[dict]:
    """Find relevant semantic rules using hybrid search."""
    conn = get_connection()
    try:
        tool_input_lower = tool_input.lower()
        rules = {}

        # Keyword match
        cursor = conn.execute("""
            SELECT id, description, problem, solution, tool, action, priority, prompt
            FROM rules
            WHERE active = 1 AND type = 'semantic'
            AND (tool IS NULL OR tool = ?)
        """, (tool_name,))

        skip = {'a', 'an', 'the', 'for', 'to', 'of', 'in', 'on', 'with', 'use', 'always', 'never'}

        for row in cursor.fetchall():
            desc_words = set(row['description'].lower().split())
            input_words = set(tool_input_lower.split())
            desc_keywords = desc_words - skip

            if desc_keywords & input_words:
                rules[row['id']] = {
                    'id': row['id'],
                    'description': row['description'],
                    'problem': row['problem'],
                    'solution': row['solution'],
                    'action': row['action'],
                    'prompt': row['prompt'],
                    'distance': 0.5,
                    'match_type': 'keyword'
                }

        # Vector search
        input_embedding = generate_embedding(f"{tool_name}: {tool_input}")
        embedding_bytes = serialize_vector(input_embedding)

        cursor = conn.execute("""
            SELECT r.id, r.description, r.problem, r.solution, r.action, r.prompt, re.distance
            FROM rule_embeddings re
            JOIN rules r ON r.id = re.rule_id
            WHERE re.embedding MATCH ?
            AND re.k = ?
            AND r.active = 1
            AND r.type = 'semantic'
            AND (r.tool IS NULL OR r.tool = ?)
            ORDER BY re.distance
        """, (embedding_bytes, top_k, tool_name))

        for row in cursor.fetchall():
            if row['id'] not in rules:
                rules[row['id']] = {
                    'id': row['id'],
                    'description': row['description'],
                    'problem': row['problem'],
                    'solution': row['solution'],
                    'action': row['action'],
                    'prompt': row['prompt'],
                    'distance': row['distance'],
                    'match_type': 'vector'
                }

        return list(rules.values())
    finally:
        conn.close()


async def check_semantic_rules(tool_name: str, tool_input: str) -> RuleDecision:
    """Check tool input against semantic rules using LLM."""
    rules = find_semantic_rules(tool_name, tool_input)

    if not rules:
        return RuleDecision(approved=True, action="allow", comment="No semantic rules")

    close_rules = [r for r in rules if r.get('match_type') == 'keyword' or (r['distance'] and r['distance'] < 0.8)]

    if not close_rules:
        return RuleDecision(approved=True, action="allow", comment="No relevant rules")

    # Limit to 5 rules, compact format
    close_rules = close_rules[:5]
    rules_text = []
    for r in close_rules:
        rule_str = f"- #{r['id']} ({r['action']}): {r['description'][:80]}"
        if r.get('solution'):
            rule_str += f" → {r['solution'][:60]}"
        rules_text.append(rule_str)

    agent = get_rule_agent()
    result = await agent.run(f"""Tool: {tool_name}
Input: {tool_input[:500]}

Rules:
{chr(10).join(rules_text)}

Violates any rule?""")

    return result.output


async def check_with_agent(tool_name: str, tool_input: str, justification: str = None) -> RuleDecision:
    """Check tool input against all rules."""
    init_db()

    # 1. Regex rules (fast) - may return rules needing LLM review
    passed, reason, action, llm_reviews = check_regex_rules(tool_name, tool_input)
    if not passed:
        return RuleDecision(approved=False, action=action or "block", comment=reason or "Blocked")

    # 2. Find semantic rules (embedding search)
    semantic_rules = find_semantic_rules(tool_name, tool_input)
    close_semantic = [r for r in semantic_rules if r.get('match_type') == 'keyword' or (r['distance'] and r['distance'] < 0.8)]

    # 3. Combine all rules needing LLM review into ONE call
    all_rules_for_llm = []

    # Add llm_review regex rules
    for r in llm_reviews:
        all_rules_for_llm.append({
            'id': r['id'],
            'description': r['description'],
            'action': r['action'],
            'prompt': r.get('prompt'),
            'hard': r.get('hard', 0),
            'source': 'regex+llm'
        })

    # Add semantic rules
    for r in close_semantic:
        all_rules_for_llm.append({
            'id': r['id'],
            'description': r['description'],
            'action': r['action'],
            'prompt': r.get('prompt'),
            'problem': r.get('problem'),
            'solution': r.get('solution'),
            'hard': r.get('hard', 0),
            'source': 'semantic'
        })

    if not all_rules_for_llm:
        return RuleDecision(approved=True, action="allow", comment="No rules to check")

    # Single LLM call for all rules
    return await check_rules_with_llm(all_rules_for_llm, tool_name, tool_input, justification)


async def check_rules_with_llm(rules: list[dict], tool_name: str, tool_input: str, justification: str = None) -> RuleDecision:
    """Single LLM call to evaluate all identified rules."""
    # Limit to 5 most relevant rules
    rules = rules[:5]
    rules_text = []
    for r in rules:
        hard_label = "HARD" if r.get('hard') else "SOFT"
        rule_str = f"- [{hard_label}] #{r['id']} ({r['action']}): {r['description'][:80]}"
        if r.get('solution'):
            rule_str += f" → {r['solution'][:60]}"
        rules_text.append(rule_str)

    justification_text = ""
    if justification:
        justification_text = f"\nJustification: {justification[:200]}\n"

    agent = get_rule_agent()
    result = await agent.run(f"""Tool: {tool_name}
Input: {tool_input[:800]}
{justification_text}
Rules:
{chr(10).join(rules_text)}

Violates any rule?""")

    return result.output



def update_rule_embedding(rule_id: int, description: str, force: bool = True):
    """Update or create embedding for a rule.
    
    If force is False, only generates if it doesn't exist.
    """
    conn = get_connection()
    try:
        if not force:
            existing = conn.execute(
                "SELECT 1 FROM rule_embeddings WHERE rule_id = ?", (rule_id,)
            ).fetchone()
            if existing:
                return

        embedding = generate_embedding(description)
        embedding_bytes = serialize_vector(embedding)

        conn.execute("DELETE FROM rule_embeddings WHERE rule_id = ?", (rule_id,))
        conn.execute("""
            INSERT INTO rule_embeddings (rule_id, embedding) 
            VALUES (?, ?)
        """, (rule_id, embedding_bytes))
        conn.commit()
    finally:
        conn.close()


def sync_all_rule_embeddings():
    """Generate embeddings for all rules that don't have them."""
    conn = get_connection()
    try:
        cursor = conn.execute("""
            SELECT r.id, r.description
            FROM rules r
            LEFT JOIN rule_embeddings re ON r.id = re.rule_id
            WHERE re.rule_id IS NULL AND r.active = 1
        """)

        for row in cursor.fetchall():
            update_rule_embedding(row['id'], row['description'], force=False)
    finally:
        conn.close()


if __name__ == "__main__":
    import asyncio
    import sys

    print("Syncing rule embeddings...")
    sync_all_rule_embeddings()
    print("Done!")

    if len(sys.argv) > 2:
        tool = sys.argv[1]
        inp = " ".join(sys.argv[2:])
        result = asyncio.run(check_with_agent(tool, inp))
        print(f"\nDecision: {'APPROVED' if result.approved else 'REJECTED'}")
        print(f"Action: {result.action}")
        print(f"Comment: {result.comment}")
