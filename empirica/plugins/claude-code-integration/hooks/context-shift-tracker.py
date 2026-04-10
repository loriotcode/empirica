#!/usr/bin/env python3
"""UserPromptSubmit hook: Track context shifts in epistemic transactions.

Classifies user prompts as:
- SOLICITED: AI asked a question (AskUserQuestion) → user responded
- UNSOLICITED: User initiated a new prompt without being asked

Uses a purely structural signal — no keyword matching:
- sentinel-gate.py sets pending_user_response=True when AskUserQuestion fires
- This hook checks that flag on each UserPromptSubmit event

Counts are stored in the hook_counters file (separate from transaction lifecycle).
POSTFLIGHT reads counters for calibration; next-PREFLIGHT uses them for divergence.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

# Add lib folder to path for shared modules
_lib_path = Path(__file__).parent.parent / 'lib'
if str(_lib_path) not in sys.path:
    sys.path.insert(0, str(_lib_path))

from project_resolver import get_instance_id, _get_instance_suffix


def _find_transaction_file(claude_session_id: 'str | None' = None) -> 'Path | None':
    """Find the active transaction file using the same priority as sentinel-gate."""
    instance_id = get_instance_id()
    suffix = _get_instance_suffix()

    # Try 1: active_work file for project_path
    if claude_session_id:
        aw_file = Path.home() / '.empirica' / f'active_work_{claude_session_id}.json'
        if aw_file.exists():
            try:
                with open(aw_file, 'r') as f:
                    pp = json.load(f).get('project_path')
                if pp:
                    candidate = Path(pp) / '.empirica' / f'active_transaction{suffix}.json'
                    if candidate.exists():
                        return candidate
            except Exception:
                pass

    # Try 2: instance_projects mapping
    if instance_id:
        ip_file = Path.home() / '.empirica' / 'instance_projects' / f'{instance_id}.json'
        if ip_file.exists():
            try:
                with open(ip_file, 'r') as f:
                    pp = json.load(f).get('project_path')
                if pp:
                    candidate = Path(pp) / '.empirica' / f'active_transaction{suffix}.json'
                    if candidate.exists():
                        return candidate
            except Exception:
                pass

    # Try 3: global fallback
    candidate = Path.home() / '.empirica' / f'active_transaction{suffix}.json'
    if candidate.exists():
        return candidate

    return None


def main():
    hook_input = json.loads(sys.stdin.read() or '{}')
    claude_session_id = hook_input.get('session_id')

    # Context window monitoring: read from instance-isolated statusline state file
    # Statusline writes ~/.empirica/context_usage{suffix}.json on each refresh
    output = {}
    try:
        import time as _time
        suffix = _get_instance_suffix()
        state_file = Path.home() / '.empirica' / f'context_usage{suffix}.json'
        if state_file.exists():
            state = json.loads(state_file.read_text())
            used_pct = state.get('used_percentage', 0)
            state_age = _time.time() - state.get('timestamp', 0)
            # Informational only — no static thresholds.
            # The AI decides when to suggest compaction based on:
            # - current work context (mid-task vs between tasks)
            # - epistemic density (how much useful context is loaded)
            # - transaction state (open vs closed)
            # We just provide the data point.
            if state_age < 60 and used_pct > 0:
                output = {
                    "hookSpecificOutput": {
                        "hookEventName": "UserPromptSubmit",
                        "additionalContext": f"context: {int(used_pct)}%"
                    }
                }
    except Exception:
        pass

    tx_path = _find_transaction_file(claude_session_id)
    if not tx_path:
        # No active transaction — still output context warning if applicable
        print(json.dumps(output))
        return

    try:
        # READ transaction file (read-only — check status only)
        with open(tx_path, 'r') as f:
            tx = json.load(f)

        if tx.get('status') != 'open':
            print(json.dumps({}))
            return

        # READ-MODIFY-WRITE the hook counters file (hook-owned, no race with POSTFLIGHT)
        suffix = _get_instance_suffix()
        counters_path = tx_path.parent / f'hook_counters{suffix}.json'
        counters = {}
        if counters_path.exists():
            try:
                with open(counters_path, 'r') as f:
                    counters = json.load(f)
            except Exception:
                counters = {}

        # Check the structural signal (set by sentinel on AskUserQuestion)
        was_solicited = counters.pop('pending_user_response', False)

        if was_solicited:
            counters['solicited_prompt_count'] = counters.get('solicited_prompt_count', 0) + 1
        else:
            counters['unsolicited_prompt_count'] = counters.get('unsolicited_prompt_count', 0) + 1

        # Atomic write-back to counters file (NOT the transaction file)
        fd, tmp = tempfile.mkstemp(dir=str(counters_path.parent))
        try:
            with os.fdopen(fd, 'w') as tf:
                json.dump(counters, tf, indent=2)
            os.replace(tmp, str(counters_path))
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    except Exception:
        pass

    print(json.dumps(output))


if __name__ == '__main__':
    main()
