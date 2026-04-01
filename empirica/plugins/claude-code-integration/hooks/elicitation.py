#!/usr/bin/env python3
"""
Empirica Elicitation Hook — Objective uncertainty measurement.

Fires when Claude asks the user a question (AskUserQuestion).
This is direct evidence of uncertainty — every question = something
the AI doesn't know.

Integration:
1. Auto-log as unknown (empirica unknown-log)
2. Search Qdrant for prior answers (inject if found)
3. Count questions per transaction for grounded uncertainty metric

Input: TBD (new hook — logging all input for discovery)
Can block: TBD
"""

import json
import logging
import subprocess
import sys
from datetime import datetime
from pathlib import Path

LOG_DIR = Path.home() / '.empirica' / 'logs'
LOG_DIR.mkdir(parents=True, exist_ok=True)
logger = logging.getLogger('empirica.elicitation')
handler = logging.FileHandler(LOG_DIR / 'elicitation.log')
handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
logger.addHandler(handler)
logger.setLevel(logging.DEBUG)

# Counter file for tracking questions per transaction
COUNTER_FILE = Path.home() / '.empirica' / 'elicitation_counter.json'


def _read_counter() -> dict:
    """Read elicitation counter."""
    if COUNTER_FILE.exists():
        try:
            with open(COUNTER_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {"count": 0, "questions": [], "transaction_id": None}


def _write_counter(counter: dict):
    """Write elicitation counter."""
    COUNTER_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(COUNTER_FILE, 'w') as f:
        json.dump(counter, f, indent=2)


def main():
    try:
        hook_input = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError):
        hook_input = {}

    # Log EVERYTHING — we're discovering the payload
    logger.info(f"=== Elicitation Event ===")
    logger.info(f"Full input: {json.dumps(hook_input, indent=2)}")
    logger.info(f"Keys: {list(hook_input.keys())}")

    # Try to extract question text from various possible payload shapes
    question = (
        hook_input.get('question') or
        hook_input.get('prompt') or
        hook_input.get('message') or
        hook_input.get('text') or
        ''
    )

    # Also check for questions array (AskUserQuestion sends multiple)
    questions = hook_input.get('questions', [])
    if questions and not question:
        question = questions[0].get('question', '') if isinstance(questions[0], dict) else str(questions[0])

    if question:
        logger.info(f"Question: {question}")

        # Auto-log as unknown
        try:
            subprocess.run(
                ['empirica', 'unknown-log',
                 '--unknown', f'[Elicitation] {question[:500]}'],
                capture_output=True, timeout=5
            )
            logger.info("  Logged as unknown")
        except Exception as e:
            logger.warning(f"  Failed to log unknown: {e}")

        # Update counter
        counter = _read_counter()
        counter['count'] += 1
        counter['questions'].append({
            'question': question[:200],
            'timestamp': datetime.now().isoformat()
        })
        # Keep last 50 questions
        counter['questions'] = counter['questions'][-50:]
        _write_counter(counter)

        # Search Qdrant for prior answers (if available)
        try:
            result = subprocess.run(
                ['empirica', 'project-search',
                 '--task', question[:200],
                 '--output', 'json'],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                search_data = json.loads(result.stdout)
                hits = search_data.get('results', [])
                if hits:
                    # Found prior knowledge — inject as context
                    top_hit = hits[0]
                    content = top_hit.get('content', '')[:500]
                    logger.info(f"  Qdrant hit: {content[:100]}...")
                    # Output context for Claude to consider
                    print(json.dumps({
                        "systemMessage": (
                            f"**Prior knowledge found** for this question:\n"
                            f"> {content}\n\n"
                            f"Consider if this answers the question before asking the user."
                        )
                    }))
                    sys.exit(0)
        except Exception as e:
            logger.debug(f"  Qdrant search failed (expected if not available): {e}")

    # Allow elicitation to proceed
    print(json.dumps({}))
    sys.exit(0)


if __name__ == '__main__':
    main()
