#!/usr/bin/env python3
"""
Empirica ElicitationResult Hook — Auto-log answers as findings/decisions.

Fires when the user answers a question from Claude.
This closes the uncertainty loop:
  Elicitation (question) → ElicitationResult (answer)
  unknown-log → finding-log or decision-log

Input: TBD (new hook — logging all input for discovery)
"""

import json
import logging
import subprocess
import sys
from pathlib import Path

LOG_DIR = Path.home() / '.empirica' / 'logs'
LOG_DIR.mkdir(parents=True, exist_ok=True)
logger = logging.getLogger('empirica.elicitation-result')
handler = logging.FileHandler(LOG_DIR / 'elicitation-result.log')
handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
logger.addHandler(handler)
logger.setLevel(logging.DEBUG)


def main():
    try:
        hook_input = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError):
        hook_input = {}

    # Log EVERYTHING — we're discovering the payload
    logger.info("=== ElicitationResult Event ===")
    logger.info(f"Full input: {json.dumps(hook_input, indent=2)}")
    logger.info(f"Keys: {list(hook_input.keys())}")

    # Try to extract answer from various possible payload shapes
    answer = (
        hook_input.get('answer') or
        hook_input.get('result') or
        hook_input.get('response') or
        hook_input.get('selected') or
        ''
    )

    # Check for answers dict (AskUserQuestion returns answers keyed by question)
    answers = hook_input.get('answers', {})
    if answers and not answer:
        # Take first answer
        answer = next(iter(answers.values()), '')

    question = hook_input.get('question', '')

    if answer:
        logger.info(f"Answer: {answer}")
        logger.info(f"Question: {question}")

        # Log as finding (user's answer resolves an unknown)
        finding_text = f"[User answer] {answer[:400]}"
        if question:
            finding_text = f"[User answer to '{question[:100]}'] {answer[:300]}"

        try:
            subprocess.run(
                ['empirica', 'finding-log',
                 '--finding', finding_text,
                 '--impact', '0.5'],
                capture_output=True, timeout=5
            )
            logger.info("  Logged as finding")
        except Exception as e:
            logger.warning(f"  Failed to log finding: {e}")

    # Allow result to proceed
    print(json.dumps({}))
    sys.exit(0)


if __name__ == '__main__':
    main()
