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

from project_resolver import _get_instance_suffix, get_instance_id  # noqa: E402 — after sys.path setup


def _find_transaction_file(claude_session_id: 'str | None' = None) -> 'Path | None':
    """Find the active transaction file using the same priority as sentinel-gate."""
    instance_id = get_instance_id()
    suffix = _get_instance_suffix()

    # Try 1: active_work file for project_path
    if claude_session_id:
        aw_file = Path.home() / '.empirica' / f'active_work_{claude_session_id}.json'
        if aw_file.exists():
            try:
                with open(aw_file) as f:
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
                with open(ip_file) as f:
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
                ctx_msg = f"context: {int(used_pct)}%"
                # At >85% context, advise auto-switching to CWD project
                # if CWD differs from active transaction project
                if used_pct > 85:
                    try:
                        cwd = str(Path.cwd().resolve())
                        tx_file = _find_transaction_file(claude_session_id)
                        if tx_file:
                            tx_data = json.loads(Path(tx_file).read_text())
                            tx_project = tx_data.get('project_path', '')
                            if tx_project and str(Path(tx_project).resolve()) != cwd:
                                ctx_msg += (
                                    f" | CWD project differs from transaction project"
                                    f" — consider project-switch to {Path(cwd).name}"
                                    f" before compaction"
                                )
                    except Exception:
                        pass
                output = {
                    "hookSpecificOutput": {
                        "hookEventName": "UserPromptSubmit",
                        "additionalContext": ctx_msg
                    }
                }
    except Exception:
        pass

    tx_path = _find_transaction_file(claude_session_id)
    if not tx_path:
        # No active transaction — suggest constitution skill for orientation
        # This fires when the AI hasn't done PREFLIGHT yet, which is exactly
        # when the constitution is most valuable (routing decisions).
        skill_nudge = "no active transaction — load /empirica-constitution for orientation before PREFLIGHT"

        # Also check if the user's prompt suggests complex work
        # that would benefit from the transaction skill
        user_prompt = hook_input.get('prompt', '').lower()
        complex_work_signals = [
            'plan', 'implement', 'spec', 'transaction', 'transactions',
            'preflight', 'artifacts', 'epistemic', 'break this down',
            'how should i approach', 'decompose', 'multi-step', 'complex',
        ]
        if any(signal in user_prompt for signal in complex_work_signals):
            skill_nudge += " | complex work detected — consider /epistemic-transaction for structured decomposition"

        if output.get("hookSpecificOutput", {}).get("additionalContext"):
            output["hookSpecificOutput"]["additionalContext"] += f" | {skill_nudge}"
        else:
            output = {
                "hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit",
                    "additionalContext": skill_nudge
                }
            }

        print(json.dumps(output))
        return

    try:
        # READ transaction file (read-only — check status only)
        with open(tx_path) as f:
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
                with open(counters_path) as f:
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

        # ARTIFACT REMINDER: At configurable turn threshold, check if artifacts
        # have been logged for the current transaction. Goal-aware: reads scope
        # to determine expectation level.
        tool_calls = counters.get('tool_call_count', 0)
        reminder_turns = 15  # default
        try:
            import yaml
            for cfg_path in [
                tx_path.parent.parent / '.empirica-project' / 'PROJECT_CONFIG.yaml',
                tx_path.parent.parent / 'PROJECT_CONFIG.yaml',
            ]:
                if cfg_path.exists():
                    with open(cfg_path) as f:
                        cfg = yaml.safe_load(f) or {}
                    reminder_turns = int(cfg.get('transaction', {}).get(
                        'log_artifacts_reminder_turns', reminder_turns))
                    break
        except Exception:
            pass

        if tool_calls >= reminder_turns and not counters.get('artifact_reminded'):
            # Check artifact counts for this transaction
            try:
                tx_id = tx.get('transaction_id')
                session_id = tx.get('session_id')
                if tx_id and session_id:
                    sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent))
                    from empirica.data.session_database import SessionDatabase
                    db = SessionDatabase()
                    cursor = db.conn.cursor()
                    total = 0
                    for table in ('project_findings', 'project_unknowns', 'project_dead_ends',
                                  'mistakes_made', 'assumptions', 'decisions'):
                        try:
                            cursor.execute(
                                f"SELECT COUNT(*) FROM {table} WHERE session_id = ? AND transaction_id = ?",
                                (session_id, tx_id))
                            total += cursor.fetchone()[0]
                        except Exception:
                            pass
                    db.close()

                    if total == 0:
                        # Goal-aware: check scope for expectation
                        goal_hint = ""
                        try:
                            cursor2 = SessionDatabase().conn.cursor()
                            cursor2.execute("""
                                SELECT objective, scope FROM goals
                                WHERE transaction_id = ? AND status = 'in_progress'
                                LIMIT 1
                            """, (tx_id,))
                            row = cursor2.fetchone()
                            if row:
                                scope = json.loads(row[1]) if row[1] else {}
                                breadth = scope.get('breadth', 0.3)
                                if breadth >= 0.5:
                                    goal_hint = f" Working on '{row[0][:50]}...' (breadth {breadth}) — decisions and assumptions are likely worth capturing."
                                else:
                                    goal_hint = f" Working on '{row[0][:50]}...' — at minimum log a finding."
                        except Exception:
                            pass

                        reminder = (
                            f"Artifact reminder: {tool_calls} tool calls, 0 artifacts logged "
                            f"in this transaction.{goal_hint} "
                            f"Commands: finding-log, decision-log, assumption-log, unknown-log"
                        )
                        if output.get("hookSpecificOutput", {}).get("additionalContext"):
                            output["hookSpecificOutput"]["additionalContext"] += f" | {reminder}"
                        else:
                            output.setdefault("hookSpecificOutput", {})["additionalContext"] = reminder

                        counters['artifact_reminded'] = True
                        # Re-write counters with reminder flag
                        fd2, tmp2 = tempfile.mkstemp(dir=str(counters_path.parent))
                        try:
                            with os.fdopen(fd2, 'w') as tf2:
                                json.dump(counters, tf2, indent=2)
                            os.replace(tmp2, str(counters_path))
                        except BaseException:
                            try:
                                os.unlink(tmp2)
                            except OSError:
                                pass
            except Exception:
                pass

    except Exception:
        pass

    print(json.dumps(output))


if __name__ == '__main__':
    main()
