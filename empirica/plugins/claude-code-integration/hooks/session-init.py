#!/usr/bin/env python3
"""
Empirica Session Init Hook - Auto-creates session + bootstrap for new conversations

This hook runs on new/fresh session starts (not compactions) and:
1. Creates a new Empirica session
2. Runs project-bootstrap to load context
3. Prompts the AI to run PREFLIGHT with loaded context

This ensures every conversation starts with proper epistemic baseline.
"""

import json
import sys
import subprocess
import os
import re
import shutil
from pathlib import Path
from datetime import datetime

# Import shared utilities from plugin lib
sys.path.insert(0, str(Path(__file__).parent.parent / 'lib'))
from project_resolver import get_instance_id, find_project_root  # noqa: E402

def archive_stale_plans() -> list:
    """
    Archive plan files whose goals are complete.

    Scans ~/.claude/plans/ for .md files, extracts goal_id from content,
    checks if goal is complete, and moves to archive if so.

    Returns list of archived plan names.
    """
    plans_dir = Path.home() / ".claude" / "plans"
    archive_dir = plans_dir / "archive"

    if not plans_dir.exists():
        return []

    archived = []
    goal_id_pattern = re.compile(r'\*\*Goal ID:\*\*\s*`([a-f0-9-]+)`')

    for plan_file in plans_dir.glob("*.md"):
        if plan_file.name.startswith("."):
            continue

        try:
            content = plan_file.read_text()

            # Extract goal_id if present
            match = goal_id_pattern.search(content)
            if not match:
                continue

            goal_id = match.group(1)

            # Check if goal exists and is complete
            result = subprocess.run(
                ['empirica', 'goals-progress', '--goal-id', goal_id, '--output', 'json'],
                capture_output=True, text=True, timeout=5
            )

            if result.returncode == 0:
                try:
                    goal_data = json.loads(result.stdout)
                    status = goal_data.get('status', '')
                    completion_pct = goal_data.get('completion_percentage', 0)

                    # Archive if completed or all subtasks done
                    if status == 'completed' or completion_pct >= 100:
                        archive_dir.mkdir(parents=True, exist_ok=True)
                        dest = archive_dir / plan_file.name
                        shutil.move(str(plan_file), str(dest))
                        archived.append(plan_file.name)
                except json.JSONDecodeError:
                    pass
        except Exception:
            continue

    return archived


def create_session_and_bootstrap(ai_id: str, project_id: str = None) -> dict:
    """
    Create session + run bootstrap in sequence.

    Returns dict with session_id, bootstrap_output, error
    """
    result = {
        "session_id": None,
        "bootstrap_output": None,
        "project_context": None,
        "error": None
    }

    try:
        # Create session
        create_cmd = subprocess.run(
            ['empirica', 'session-create', '--ai-id', ai_id, '--output', 'json'],
            capture_output=True, text=True, timeout=15
        )

        if create_cmd.returncode != 0:
            result["error"] = f"session-create failed: {create_cmd.stderr}"
            return result

        create_output = json.loads(create_cmd.stdout)
        session_id = create_output.get('session_id')

        if not session_id:
            result["error"] = "session-create returned no session_id"
            return result

        result["session_id"] = session_id

        # Run bootstrap
        bootstrap_cmd = subprocess.run(
            ['empirica', 'project-bootstrap', '--session-id', session_id, '--output', 'json'],
            capture_output=True, text=True, timeout=30
        )

        if bootstrap_cmd.returncode == 0:
            try:
                bootstrap_data = json.loads(bootstrap_cmd.stdout)
                result["bootstrap_output"] = bootstrap_data

                # Extract key context
                result["project_context"] = {
                    "goals": bootstrap_data.get("goals", [])[:3],
                    "findings": bootstrap_data.get("findings", [])[:5],
                    "unknowns": bootstrap_data.get("unknowns", [])[:5]
                }
            except json.JSONDecodeError:
                result["bootstrap_output"] = {"raw": bootstrap_cmd.stdout[:500]}

    except subprocess.TimeoutExpired:
        result["error"] = "Command timed out"
    except Exception as e:
        result["error"] = str(e)

    return result


def format_context(ctx: dict) -> str:
    """Format project context for prompt."""
    if not ctx:
        return "  (No context available)"

    parts = []

    if ctx.get("goals"):
        parts.append("**Active Goals:**")
        for g in ctx["goals"]:
            obj = g.get("objective", g) if isinstance(g, dict) else str(g)
            parts.append(f"  - {obj[:100]}")

    if ctx.get("findings"):
        parts.append("\n**Recent Findings:**")
        for f in ctx["findings"]:
            finding = f.get("finding", f) if isinstance(f, dict) else str(f)
            parts.append(f"  - {finding[:100]}")

    if ctx.get("unknowns"):
        parts.append("\n**Open Unknowns:**")
        for u in ctx["unknowns"]:
            unknown = u.get("unknown", u) if isinstance(u, dict) else str(u)
            parts.append(f"  - {unknown[:100]}")

    return "\n".join(parts) if parts else "  (No context loaded)"


def _write_instance_projects(project_path: str, claude_session_id: str, empirica_session_id: str) -> bool:
    """
    Write instance isolation files. Establishes linkage between Claude's
    conversation ID and the Empirica session — critical for project-switch,
    statusline, and sentinel to work correctly.

    Works with or without tmux. Falls back to TTY or 'default' instance.
    """
    try:
        instance_id = get_instance_id()
        instance_dir = Path.home() / '.empirica' / 'instance_projects'
        instance_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        instance_file = instance_dir / f'{instance_id}.json'

        # Get TTY key if available
        tty_key = None
        try:
            tty_path = os.ttyname(sys.stdin.fileno())
            tty_key = tty_path.replace('/', '-').lstrip('-')
        except:
            pass

        instance_data = {
            'project_path': project_path,
            'tty_key': tty_key,
            'claude_session_id': claude_session_id,
            'empirica_session_id': empirica_session_id,
            'instance_id': instance_id,
            'timestamp': datetime.now().isoformat()
        }
        with open(instance_file, 'w') as f:
            json.dump(instance_data, f, indent=2)
        os.chmod(instance_file, 0o600)

        # Write session-specific active_work file (with claude_session_id suffix)
        folder_name = Path(project_path).name
        active_work_data = {
            'project_path': project_path,
            'folder_name': folder_name,
            'claude_session_id': claude_session_id,
            'empirica_session_id': empirica_session_id,
            'source': 'session-init',
            'timestamp': datetime.now().isoformat(),
            'timestamp_epoch': datetime.now().timestamp()
        }

        if claude_session_id:
            active_work_file = Path.home() / '.empirica' / f'active_work_{claude_session_id}.json'
            with open(active_work_file, 'w') as f:
                json.dump(active_work_data, f, indent=2)
            os.chmod(active_work_file, 0o600)

        # Generic active_work.json only in headless mode (no terminal identity)
        # In interactive mode, instance_projects + active_work_{uuid} handle everything
        if not instance_id and not claude_session_id:
            generic_file = Path.home() / '.empirica' / 'active_work.json'
            with open(generic_file, 'w') as f:
                json.dump(active_work_data, f, indent=2)
            os.chmod(generic_file, 0o600)

        # Also write claude_session_id to TTY session file if available.
        # CLI commands (session-create) write TTY session but WITHOUT claude_session_id
        # because they don't have access to it. Hooks DO have it from stdin.
        # Without this, project-switch via Bash tool can't reverse-lookup instance_id.
        if tty_key and claude_session_id:
            tty_sessions_dir = Path.home() / '.empirica' / 'tty_sessions'
            tty_sessions_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            tty_session_file = tty_sessions_dir / f'{tty_key}.json'

            # Read existing data (session-create may have written it already)
            tty_data = {}
            if tty_session_file.exists():
                try:
                    with open(tty_session_file, 'r') as f:
                        tty_data = json.load(f)
                except Exception:
                    pass

            # Update with claude_session_id and instance_id (preserving other fields)
            tty_data['claude_session_id'] = claude_session_id
            tty_data['instance_id'] = instance_id
            tty_data['project_path'] = project_path
            tty_data['empirica_session_id'] = empirica_session_id
            tty_data['tty_key'] = tty_key
            tty_data['timestamp'] = datetime.now().isoformat()
            tty_data['pid'] = os.getpid()
            tty_data['ppid'] = os.getppid()

            with open(tty_session_file, 'w') as f:
                json.dump(tty_data, f, indent=2)
            os.chmod(tty_session_file, 0o600)

        return True
    except Exception as e:
        print(f"Warning: Failed to write instance_projects: {e}", file=sys.stderr)
        return False


def _detect_existing_session(claude_session_id: str, project_root: Path) -> dict:
    """Check if an Empirica session already exists for this conversation.

    On 'resume' (continued conversation in new terminal), the session exists
    in the DB but the anchor files (active_work_{uuid}, instance_projects)
    are stale because the WINDOWID changed. We detect this to avoid creating
    a duplicate session.

    Returns dict with session_id if found, empty dict if not.
    """
    if not claude_session_id:
        return {}

    # Check active_work_{uuid} file first (fastest)
    active_work_file = Path.home() / '.empirica' / f'active_work_{claude_session_id}.json'
    if active_work_file.exists():
        try:
            with open(active_work_file, 'r') as f:
                data = json.load(f)
            session_id = data.get('empirica_session_id')
            if session_id:
                return {'session_id': session_id, 'source': 'active_work'}
        except Exception:
            pass

    # Check active_session file (written by session-create)
    try:
        from project_resolver import _get_instance_suffix
        suffix = _get_instance_suffix()
    except ImportError:
        suffix = ''

    # Scan ALL active_session files — the old WINDOWID's file may still exist
    for as_file in Path.home().glob('.empirica/active_session_*'):
        try:
            with open(as_file, 'r') as f:
                data = json.load(f)
            # Match by project_path — same project means same logical session
            if data.get('project_path') == str(project_root):
                session_id = data.get('session_id')
                if session_id:
                    return {'session_id': session_id, 'source': 'active_session'}
        except Exception:
            continue

    # Check DB directly via CLI
    try:
        result = subprocess.run(
            ['empirica', 'session-list', '--output', 'json', '--limit', '5'],
            capture_output=True, text=True, timeout=10,
            cwd=str(project_root)
        )
        if result.returncode == 0:
            sessions = json.loads(result.stdout)
            session_list = sessions.get('sessions', [])
            for s in session_list:
                if s.get('status') == 'active':
                    return {'session_id': s.get('session_id'), 'source': 'db_active'}
    except Exception:
        pass

    return {}


def main():
    """Main session init logic.

    Handles both 'startup' (fresh conversation) and 'resume' (continued
    conversation in new terminal). On resume, detects existing session and
    updates anchor files without creating a duplicate.
    """
    hook_input = {}
    try:
        hook_input = json.loads(sys.stdin.read())
    except:
        pass

    # Extract claude_session_id from hook input (critical for instance isolation)
    claude_session_id = hook_input.get('session_id')

    # Detect if this is a resume (continued conversation)
    event_type = hook_input.get('type', 'startup')
    is_resume = event_type == 'resume'

    # Find project root (uses instance-aware resolution to survive CWD resets)
    # session-init needs CWD and git root fallbacks for first-time projects
    project_root = find_project_root(claude_session_id, allow_cwd_fallback=True, allow_git_root=True)
    os.chdir(project_root)

    ai_id = os.getenv('EMPIRICA_AI_ID', 'claude-code')

    # Opportunistic cleanup: remove stale instance_projects for dead tmux panes
    stale_removed = 0
    try:
        sys.path.insert(0, str(Path.home() / 'empirical-ai' / 'empirica'))
        from empirica.utils.session_resolver import InstanceResolver as R
        stale_removed = R.cleanup_stale_instances()
        # DB-based cleanup: remove active_work, instance_projects (non-tmux), and
        # active_session files for ended sessions (no open transaction referencing them)
        stale_removed += R.cleanup_stale_files(current_claude_session_id=claude_session_id)
    except Exception:
        pass  # Cleanup failure is non-fatal

    # Archive stale plans (whose goals are complete)
    archived_plans = archive_stale_plans()

    # RESUME PATH: Detect existing session and update anchor files only
    if is_resume:
        existing = _detect_existing_session(claude_session_id, project_root)
        if existing.get('session_id'):
            session_id = existing['session_id']
            # Update anchor files for the new WINDOWID/terminal
            _write_instance_projects(str(project_root), claude_session_id, session_id)

            # Run bootstrap to load context
            bootstrap_output = None
            try:
                bootstrap_cmd = subprocess.run(
                    ['empirica', 'project-bootstrap', '--session-id', session_id, '--output', 'json'],
                    capture_output=True, text=True, timeout=30,
                    cwd=str(project_root)
                )
                if bootstrap_cmd.returncode == 0:
                    bootstrap_output = json.loads(bootstrap_cmd.stdout)
            except Exception:
                pass

            output = {
                "ok": True,
                "session_id": session_id,
                "resumed": True,
                "bootstrap_complete": bootstrap_output is not None,
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "additionalContext": f"""
## Session Resumed

**Session ID:** `{session_id}` (existing, from {existing.get('source', 'unknown')})
**Project:** {project_root}

Anchor files updated for new terminal. Existing session and transaction state preserved.

**Note:** If you need a fresh session, run `empirica session-create --ai-id {ai_id}`.
"""
                }
            }

            print(f"""
🔄 Empirica: Session Resumed

✅ Session: {session_id} (anchored to new terminal)
✅ Project: {project_root.name}
""", file=sys.stderr)

            print(json.dumps(output))
            sys.exit(0)

        # No existing session found during resume — fall through to create new one
        print(f"⚠️  Resume: no existing session found for {project_root.name}, creating new one", file=sys.stderr)

    # Create session and bootstrap
    result = create_session_and_bootstrap(ai_id)

    # CRITICAL: Write instance_projects IMMEDIATELY after session creation
    # This establishes the linkage that project-switch, statusline, and sentinel need.
    # Works with or without claude_session_id — fallback writes generic active_work.json
    if result.get("session_id"):
        _write_instance_projects(str(project_root), claude_session_id, result["session_id"])

    if result.get("error"):
        # Error creating session - provide fallback guidance
        output = {
            "ok": False,
            "error": result["error"],
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": f"""
## Session Init Failed

Error: {result["error"]}

**Manual Setup Required:**

```bash
empirica session-create --ai-id {ai_id} --output json
empirica project-bootstrap --session-id <SESSION_ID> --output json
empirica preflight-submit - << 'EOF'
{{
  "session_id": "<SESSION_ID>",
  "task_context": "<task>",
  "vectors": {{ "know": 0.3, "uncertainty": 0.6, "context": 0.3, "engagement": 0.7 }},
  "reasoning": "New session baseline"
}}
EOF
```
"""
            }
        }
        print(json.dumps(output))
        sys.exit(0)

    # Initialize Context Budget Manager (bootloader phase)
    budget_summary = None
    try:
        sys.path.insert(0, str(Path.home() / 'empirical-ai' / 'empirica'))
        from empirica.core.context_budget import (
            ContextBudgetManager, ContextItem, MemoryZone, ContentType,
            InjectionChannel, estimate_tokens,
        )

        manager = ContextBudgetManager(
            session_id=result["session_id"],
            auto_subscribe=False,  # No bus in subprocess
        )

        # Register anchor zone: CLAUDE.md, calibration, session state
        manager.register_item(ContextItem(
            id="claude_md",
            zone=MemoryZone.ANCHOR,
            content_type=ContentType.SYSTEM_PROMPT,
            source="CLAUDE.md",
            channel=InjectionChannel.HOOK,
            label="CLAUDE.md system prompt + calibration",
            estimated_tokens=12000,
            epistemic_value=1.0,
            evictable=False,
        ))
        manager.register_item(ContextItem(
            id="session_state",
            zone=MemoryZone.ANCHOR,
            content_type=ContentType.CALIBRATION,
            source="session-init",
            channel=InjectionChannel.HOOK,
            label=f"Session {result['session_id'][:8]} state",
            estimated_tokens=1000,
            epistemic_value=1.0,
            evictable=False,
        ))

        # Register bootstrap context as cache items
        ctx = result.get("project_context", {})
        if ctx.get("goals"):
            for i, g in enumerate(ctx["goals"]):
                obj = g.get("objective", str(g)) if isinstance(g, dict) else str(g)
                manager.register_item(ContextItem(
                    id=f"boot_goal_{i}",
                    zone=MemoryZone.WORKING,
                    content_type=ContentType.GOAL,
                    source="project-bootstrap",
                    channel=InjectionChannel.HOOK,
                    label=obj[:80],
                    estimated_tokens=200,
                    epistemic_value=0.8,
                    evictable=False,
                ))
        if ctx.get("findings"):
            for i, f in enumerate(ctx["findings"]):
                text = f.get("finding", str(f)) if isinstance(f, dict) else str(f)
                impact = f.get("impact", 0.5) if isinstance(f, dict) else 0.5
                manager.register_item(ContextItem(
                    id=f"boot_finding_{i}",
                    zone=MemoryZone.CACHE,
                    content_type=ContentType.FINDING,
                    source="project-bootstrap",
                    channel=InjectionChannel.HOOK,
                    label=text[:80],
                    estimated_tokens=estimate_tokens(text),
                    epistemic_value=float(impact) if impact else 0.5,
                ))

        # Persist and get summary
        manager.persist_state()
        budget_summary = manager.get_inventory_summary()
    except Exception as e:
        budget_summary = {"error": str(e)}

    # Initialize System Dashboard (observability layer)
    dashboard_status = None
    try:
        from empirica.core.system_dashboard import SystemDashboard
        dashboard = SystemDashboard(
            session_id=result["session_id"],
            node_id=ai_id,
            auto_subscribe=False,  # No bus in subprocess
        )
        status = dashboard.get_system_status()
        dashboard_status = status.format_summary()
    except Exception:
        pass  # Dashboard failure is non-fatal

    # Success - generate PREFLIGHT prompt
    session_id = result["session_id"]
    context_text = format_context(result.get("project_context"))

    prompt = f"""
## New Session Initialized

**Session ID:** `{session_id}`
**Project context loaded via bootstrap**

### Project Context:
{context_text}

### REQUIRED: Run PREFLIGHT (Baseline)

Assess your epistemic state after reviewing the context above:

```bash
empirica preflight-submit - << 'EOF'
{{
  "session_id": "{session_id}",
  "task_context": "<what the user is asking for>",
  "vectors": {{
    "know": <0.0-1.0: How much do you know about this task/codebase?>,
    "uncertainty": <0.0-1.0: How uncertain are you?>,
    "context": <0.0-1.0: How well do you understand the current state?>,
    "engagement": <0.0-1.0: How engaged/aligned are you with the task?>
  }},
  "reasoning": "New session: <explain your starting epistemic state>"
}}
EOF
```

**After PREFLIGHT:** Before any Edit/Write/Bash, run CHECK to validate readiness.
"""

    output = {
        "ok": True,
        "session_id": session_id,
        "bootstrap_complete": result.get("bootstrap_output") is not None,
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": prompt
        }
    }

    # User-visible message
    archive_msg = f"\n🗄️  Archived {len(archived_plans)} stale plan(s)" if archived_plans else ""
    budget_msg = ""
    if budget_summary and not budget_summary.get("error"):
        budget_msg = f"\n📊 Budget: {budget_summary.get('tokens_used', 0):,}t used / {budget_summary.get('tokens_available', 0):,}t avail ({budget_summary.get('utilization_pct', 0)}%)"
    dash_msg = f"\n🖥️  {dashboard_status}" if dashboard_status else ""
    print(f"""
🚀 Empirica: New Session Initialized

✅ Session created: {session_id}
✅ Project context loaded{archive_msg}{budget_msg}{dash_msg}

📋 Run PREFLIGHT to establish baseline, then CHECK before actions.
""", file=sys.stderr)

    print(json.dumps(output))
    sys.exit(0)


if __name__ == '__main__':
    main()
