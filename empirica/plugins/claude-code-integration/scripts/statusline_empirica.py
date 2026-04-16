#!/usr/bin/env python3
"""
Empirica Statusline v2 - Unified Signaling with Moon Phases

Uses the shared signaling module for consistent emoji display.
Reads vectors from DB (real-time).

Display modes:
  - basic: Just confidence
  - default: Phase + key vectors + open counts
  - learning: Focus on vector changes
  - full: Everything with values

Environment:
  EMPIRICA_STATUS_MODE: basic|default|learning|full (default: default)
  EMPIRICA_AI_ID: AI identifier (default: claude-code)
  EMPIRICA_SIGNALING_LEVEL: basic|default|full (default: default)

Author: Claude Code
Date: 2025-12-30
Version: 2.1.0 (Unified Signaling)
"""

import os
import sys
from pathlib import Path

# Add empirica to path
EMPIRICA_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(EMPIRICA_ROOT))

from empirica.core.signaling import format_vectors_compact
from empirica.data.session_database import SessionDatabase


# ANSI color codes
class Colors:
    RESET = '\033[0m'
    BOLD = '\033[1m'
    GREEN = '\033[32m'
    YELLOW = '\033[33m'
    RED = '\033[31m'
    BLUE = '\033[34m'
    CYAN = '\033[36m'
    GRAY = '\033[90m'
    WHITE = '\033[37m'
    BRIGHT_GREEN = '\033[92m'
    BRIGHT_CYAN = '\033[96m'


def get_ai_id() -> str:
    """Get AI identifier from environment."""
    return os.getenv('EMPIRICA_AI_ID', 'claude-code').strip()


def get_open_counts(db: SessionDatabase, session_id: str, project_id: str | None = None) -> dict:
    """
    Get counts of open goals and unknowns for a specific project.

    Goals and unknowns are project-scoped to prevent cross-project data leakage.
    Goal-linked unknowns indicate blockers that need resolution.

    Args:
        db: Database connection
        session_id: Current session ID
        project_id: Project ID to filter by (required for accurate counts)

    Returns:
        {
            'open_goals': int,           # Goals not yet completed (project-scoped)
            'open_unknowns': int,        # Unknowns not yet resolved (project-scoped)
            'goal_linked_unknowns': int, # Unresolved unknowns linked to goals (blockers)
            'completion': float,         # Latest completion vector (0.0-1.0)
        }
    """
    cursor = db.conn.cursor()

    # Count open goals for THIS PROJECT (project-scoped, not session-scoped)
    # Use is_completed (source of truth) not status column (can be inconsistent)
    if project_id:
        cursor.execute("""
            SELECT COUNT(*)
            FROM goals
            WHERE is_completed = 0 AND project_id = ?
        """, (project_id,))
    else:
        cursor.execute("""
            SELECT COUNT(*)
            FROM goals
            WHERE is_completed = 0
        """)
    open_goals = cursor.fetchone()[0] or 0

    # Count unresolved unknowns for THIS PROJECT
    # Use project_unknowns directly (has project_id column) - no session JOIN needed
    if project_id:
        cursor.execute("""
            SELECT COUNT(*)
            FROM project_unknowns
            WHERE is_resolved = 0 AND project_id = ?
        """, (project_id,))
    else:
        cursor.execute("""
            SELECT COUNT(*)
            FROM project_unknowns
            WHERE is_resolved = 0
        """)
    open_unknowns = cursor.fetchone()[0] or 0

    # Count goal-linked unresolved unknowns (blockers) for THIS PROJECT
    if project_id:
        cursor.execute("""
            SELECT COUNT(*)
            FROM project_unknowns
            WHERE is_resolved = 0 AND goal_id IS NOT NULL AND project_id = ?
        """, (project_id,))
    else:
        cursor.execute("""
            SELECT COUNT(*)
            FROM project_unknowns
            WHERE is_resolved = 0 AND goal_id IS NOT NULL
        """)
    goal_linked_unknowns = cursor.fetchone()[0] or 0

    # Get completion from latest vector state
    cursor.execute("""
        SELECT completion
        FROM reflexes
        WHERE session_id = ?
        ORDER BY timestamp DESC
        LIMIT 1
    """, (session_id,))
    reflex_row = cursor.fetchone()
    completion = reflex_row[0] if reflex_row and reflex_row[0] is not None else 0.0

    return {
        'open_goals': open_goals,
        'open_unknowns': open_unknowns,
        'goal_linked_unknowns': goal_linked_unknowns,
        'completion': completion,
    }


def get_active_goal(db: SessionDatabase, session_id: str) -> dict | None:
    """
    Get the active goal for a session (legacy, kept for 'full' mode).

    Returns:
        {
            'goal_id': str,
            'objective': str,
            'completion': float (0.0-1.0) - from vector state, not subtasks
            'subtask_progress': (completed, total) - for reference only
        }
        or None if no active goal
    """
    cursor = db.conn.cursor()

    # Get active (non-completed) goal for this session
    cursor.execute("""
        SELECT id, objective, status
        FROM goals
        WHERE session_id = ? AND status != 'completed'
        ORDER BY created_timestamp DESC
        LIMIT 1
    """, (session_id,))
    row = cursor.fetchone()

    if not row:
        return None

    goal_id, objective, _ = row  # _ for unused status

    # Get completion from latest vector state (epistemic measure)
    # This is the AI's self-assessed completion, not mechanical subtask checkboxes
    cursor.execute("""
        SELECT completion
        FROM reflexes
        WHERE session_id = ?
        ORDER BY timestamp DESC
        LIMIT 1
    """, (session_id,))
    reflex_row = cursor.fetchone()
    completion = reflex_row[0] if reflex_row and reflex_row[0] is not None else 0.0

    # Get subtask progress (for reference, not primary measure)
    cursor.execute("""
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed
        FROM subtasks
        WHERE goal_id = ?
    """, (goal_id,))
    subtask_row = cursor.fetchone()

    total_subtasks = subtask_row[0] if subtask_row else 0
    completed_subtasks = subtask_row[1] if subtask_row else 0

    return {
        'goal_id': goal_id,
        'objective': objective,
        'completion': completion,
        'subtask_progress': (completed_subtasks, total_subtasks)
    }


def format_progress_bar(completion: float, width: int = 8) -> str:
    """
    Format completion as ASCII progress bar.

    Args:
        completion: 0.0 to 1.0
        width: number of characters for the bar

    Returns:
        String like "████░░░░ 45%"
    """
    filled = int(completion * width)
    empty = width - filled

    bar = "█" * filled + "░" * empty
    pct = int(completion * 100)

    # Color based on progress
    if completion >= 0.75:
        color = Colors.BRIGHT_GREEN
    elif completion >= 0.5:
        color = Colors.GREEN
    elif completion >= 0.25:
        color = Colors.YELLOW
    else:
        color = Colors.GRAY

    return f"{color}{bar}{Colors.RESET} {pct}%"


def format_open_counts(open_counts: dict | None) -> str:
    """
    Format open goals and unknowns as actionable counts.

    Shows what needs to be closed - useful for peeking into AI reality.

    Returns:
        String like "🎯3 ❓6/4" (3 goals, 6 unknowns total, 4 goal-linked blockers)
    """
    if not open_counts:
        return f"{Colors.GRAY}--{Colors.RESET}"

    goals = open_counts.get('open_goals', 0)
    unknowns = open_counts.get('open_unknowns', 0)
    goal_linked = open_counts.get('goal_linked_unknowns', 0)

    # Color code based on counts
    if goals == 0:
        goal_color = Colors.GREEN
    elif goals <= 2:
        goal_color = Colors.YELLOW
    else:
        goal_color = Colors.CYAN

    # Color unknowns based on goal-linked (blockers) count
    if goal_linked == 0:
        unknown_color = Colors.GREEN
    elif goal_linked <= 5:
        unknown_color = Colors.YELLOW
    else:
        unknown_color = Colors.CYAN

    # Format: ❓total/blockers (e.g., ❓119/70 means 119 unresolved, 70 blocking goals)
    if goal_linked > 0 and goal_linked != unknowns:
        unknown_str = f"❓{unknowns}/{goal_linked}"
    else:
        unknown_str = f"❓{unknowns}"

    return f"{goal_color}🎯{goals}{Colors.RESET} {unknown_color}{unknown_str}{Colors.RESET}"


def format_goal_progress(goal: dict, max_name_len: int = 12) -> str:
    """
    Format goal progress for statusline (legacy, used in 'full' mode).

    Returns:
        String like "auth-fix ████░░░░ 45%" or "████░░░░ 45%" if no goal name
    """
    if not goal:
        return f"{Colors.GRAY}no goal{Colors.RESET}"

    objective = goal.get('objective', '')
    completion = goal.get('completion', 0.0)

    # Truncate objective for display
    if len(objective) > max_name_len:
        name = objective[:max_name_len-2] + ".."
    else:
        name = objective

    # Simplify name: lowercase, replace spaces with dashes
    name = name.lower().replace(' ', '-')[:max_name_len]

    bar = format_progress_bar(completion, width=6)

    if name:
        return f"{name} {bar}"
    else:
        return bar


def calculate_confidence(vectors: dict) -> float:
    """
    Calculate overall confidence score from vectors.

    Formula: weighted average of key epistemic indicators
    - know (40%): How much we understand
    - 1-uncertainty (30%): Inverse of doubt
    - context (20%): How well we understand the situation
    - completion (10%): How much is done

    Returns: 0.0 to 1.0 (displayed as 0-100%)
    """
    if not vectors:
        return 0.0

    know = vectors.get('know', 0.5)
    uncertainty = vectors.get('uncertainty', 0.5)
    context = vectors.get('context', 0.5)
    completion = vectors.get('completion', 0.0)

    confidence = (
        0.40 * know +
        0.30 * (1.0 - uncertainty) +
        0.20 * context +
        0.10 * completion
    )

    return max(0.0, min(1.0, confidence))


def format_confidence(confidence: float) -> str:
    """Format confidence as colored percentage with tiered emoji."""
    pct = int(confidence * 100)

    if confidence >= 0.75:
        color = Colors.BRIGHT_GREEN
        emoji = "⚡"  # High energy/power
    elif confidence >= 0.50:
        color = Colors.GREEN
        emoji = "💡"  # Good understanding
    elif confidence >= 0.35:
        color = Colors.YELLOW
        emoji = "💫"  # Some uncertainty
    else:
        color = Colors.RED
        emoji = "🌑"  # Low confidence/dark

    return f"{emoji}{color}{pct}%{Colors.RESET}"


def _color_by_value(value: float) -> str:
    """Color a 0-1 value: green >= 0.75, yellow 0.50-0.74, red < 0.50."""
    if value >= 0.75:
        return Colors.BRIGHT_GREEN
    elif value >= 0.50:
        return Colors.YELLOW
    return Colors.RED


def get_dynamic_threshold(db) -> tuple:
    """Get Brier-based dynamic threshold for statusline display.

    Returns (know_threshold, uncertainty_threshold, threshold_color).
    Threshold color indicates calibration quality:
      Green = well-calibrated (threshold at baseline, numbers trusted)
      Yellow = moderate inflation (some miscalibration detected)
      Red = significant inflation (unreliable self-assessment)
    """
    try:
        from empirica.core.post_test.dynamic_thresholds import compute_dynamic_thresholds
        dt = compute_dynamic_thresholds(ai_id="claude-code", db=db)
        if dt.get("source") == "dynamic":
            noetic = dt.get("noetic", {})
            if noetic.get("brier_score") is not None:
                know_t = noetic["ready_know_threshold"]
                unc_t = noetic["ready_uncertainty_threshold"]
                inflation = noetic.get("threshold_inflation", 0)
                # Color by inflation level (how much the Sentinel distrusts the AI)
                if inflation <= 0.03:
                    color = Colors.BRIGHT_GREEN  # Trusted — at baseline
                elif inflation <= 0.10:
                    color = Colors.YELLOW         # Moderate inflation
                else:
                    color = Colors.RED             # Significant miscalibration
                return (know_t, unc_t, color)
    except Exception:
        pass
    return (0.70, 0.35, Colors.GRAY)  # Static fallback


def format_threshold(know_threshold: float, color: str) -> str:
    """Format threshold as colored percentage with ↕ indicator."""
    pct = int(know_threshold * 100)
    return f"{color}↕{pct}%{Colors.RESET}"


def calculate_phase_composite(vectors: dict, phase: str) -> float:
    """Calculate composite score for the current workflow phase.

    Noetic (investigating): avg of clarity, coherence, signal, density
    Praxic (acting): avg of state, change, completion, impact
    Check (readiness gate): avg of know, context, clarity, coherence, signal, density
      — CHECK evaluates readiness-to-act, not execution progress
    """
    if not vectors:
        return 0.0

    if phase == 'check':
        keys = ['know', 'context', 'clarity', 'coherence', 'signal', 'density']
    elif phase == 'noetic':
        keys = ['clarity', 'coherence', 'signal', 'density']
    else:
        keys = ['state', 'change', 'completion', 'impact']

    values = [vectors.get(k, 0.0) for k in keys if vectors.get(k) is not None]
    return sum(values) / len(values) if values else 0.0


def determine_work_phase(phase: str, gate_decision: str | None = None) -> str:
    """Determine if AI is in noetic (investigating) or praxic (acting) mode.

    Logic:
      PREFLIGHT / CHECK with investigate → noetic
      CHECK with proceed → transitioning to praxic
      POSTFLIGHT → last phase (show praxic since work completed)
      No phase → noetic (default to investigating)
    """
    if not phase:
        return 'noetic'
    if phase == 'PREFLIGHT':
        return 'noetic'
    if phase == 'CHECK':
        return 'praxic' if gate_decision == 'proceed' else 'noetic'
    if phase == 'POSTFLIGHT':
        return 'praxic'  # Work completed
    return 'noetic'


def format_phase_state(phase: str, work_phase: str, composite: float, gate_decision: str | None = None) -> str:
    """Format transaction phase + work state as compact indicator.

    Examples: PRE 🔍65% | CHK 🔍82%→ | CHK ⚙65%… | POST ⚙92% Δ ✓
    """
    # Phase abbreviation
    phase_abbrev = {
        'PREFLIGHT': 'PRE',
        'CHECK': 'CHK',
        'POSTFLIGHT': 'POST',
    }.get(phase, phase[:3] if phase else '---')

    # Work state emoji + composite
    if work_phase == 'noetic':
        emoji = "🔍"
    else:
        emoji = "⚙"

    pct = int(composite * 100)
    color = _color_by_value(composite)

    # CHECK with decision: show percentage AND transition indicator
    if phase == 'CHECK' and gate_decision:
        if gate_decision == 'proceed':
            return f"{Colors.BLUE}{phase_abbrev}{Colors.RESET} {emoji}{color}{pct}%{Colors.GREEN}→{Colors.RESET}"
        else:
            return f"{Colors.BLUE}{phase_abbrev}{Colors.RESET} {emoji}{color}{pct}%{Colors.YELLOW}…{Colors.RESET}"

    return f"{Colors.BLUE}{phase_abbrev}{Colors.RESET} {emoji}{color}{pct}%{Colors.RESET}"


def format_vector_colored(label: str, value: float) -> str:
    """Format a single vector as colored label:value%."""
    pct = int(value * 100)
    color = _color_by_value(value)
    return f"{color}{label}:{pct}%{Colors.RESET}"


def read_statusline_extensions() -> str:
    """Read extension indicators from ~/.empirica/statusline_ext/*.json.

    External packages (empirica-workspace, etc.) write JSON files here.
    Each file should contain: {"label": "WS:4", "color": "cyan"} or similar.

    Delegates to _read_statusline_extensions_data() for file reading,
    then formats labels with ANSI colors for terminal display.
    """
    extensions = _read_statusline_extensions_data()
    if not extensions:
        return ""

    parts = []
    for data in extensions:
        label = data.get('label', '')
        if label:
            parts.append(f"{Colors.CYAN}{label}{Colors.RESET}")

    return ' '.join(parts)


def _read_statusline_extensions_data() -> list:
    """Read raw extension data from ~/.empirica/statusline_ext/*.json.

    Returns a list of extension dicts as written by external packages.
    Used by build_statusline_data() for structured JSON output.

    Returns:
        [{"label": "WS:4", "color": "cyan"}, ...] or [] if none
    """
    ext_dir = Path.home() / '.empirica' / 'statusline_ext'
    if not ext_dir.exists():
        return []

    results = []
    try:
        for ext_file in sorted(ext_dir.glob('*.json')):
            try:
                import json as _json
                data = _json.loads(ext_file.read_text())
                if data.get('label'):
                    results.append(data)
            except Exception:
                continue
    except Exception:
        pass

    return results


def _resolve_claude_session_id(stdin_claude_session_id: str | None = None):
    """
    Resolve the Claude session ID from available sources.

    Priority:
    1. stdin JSON (passed by Claude Code to statusline commands)
    2. TTY session (requires TMUX_PANE)

    Returns claude_session_id or None.
    """
    if stdin_claude_session_id:
        return stdin_claude_session_id

    try:
        from empirica.utils.session_resolver import InstanceResolver as R
        tty_session = R.tty_session(warn_if_stale=False)
        if tty_session:
            return tty_session.get('claude_session_id')
    except Exception:
        pass

    return None


def _resolve_project_path(stdin_claude_session_id=None) -> str | None:
    """Resolve project path via 6-tier priority chain. Returns path or None."""
    # Priority 0: instance_projects
    try:
        import json as _json

        from empirica.utils.session_resolver import InstanceResolver as R
        inst_id = R.instance_id()
        if inst_id:
            inst_file = Path.home() / '.empirica' / 'instance_projects' / f'{inst_id}.json'
            if inst_file.exists():
                with open(inst_file) as _f:
                    pp = _json.load(_f).get('project_path')
                if pp and (Path(pp) / '.empirica' / 'sessions' / 'sessions.db').exists():
                    return pp
    except Exception:
        pass

    # Priority 1: active_work
    if stdin_claude_session_id:
        try:
            import json as _json
            aw = Path.home() / '.empirica' / f'active_work_{stdin_claude_session_id}.json'
            if aw.exists():
                with open(aw) as _f:
                    pp = _json.load(_f).get('project_path')
                if pp and (Path(pp) / '.empirica' / 'sessions' / 'sessions.db').exists():
                    return pp
        except Exception:
            pass

    # Priority 2: env var
    env_path = os.getenv('EMPIRICA_PROJECT_PATH')
    if env_path:
        return env_path

    # Priority 3: TTY session
    try:
        from empirica.utils.session_resolver import InstanceResolver as R
        tty = R.tty_session(warn_if_stale=False)
        if tty:
            pp = tty.get('project_path')
            if pp and (Path(pp) / '.empirica' / 'sessions' / 'sessions.db').exists():
                return pp
    except Exception:
        pass

    # Priority 4: path_resolver
    try:
        from empirica.config.path_resolver import get_empirica_root
        root = get_empirica_root()
        if root and root.exists() and (root / 'sessions' / 'sessions.db').exists():
            return str(root.parent)
    except Exception:
        pass

    # Priority 5: upward search
    current = Path.cwd()
    for parent in [current] + list(current.parents):
        if (parent / '.empirica' / 'sessions' / 'sessions.db').exists():
            return str(parent)
        if parent == Path.home() or parent == parent.parent:
            break
    return None


def _read_session_file(path: Path) -> str | None:
    """Read session_id from a file (JSON or plain text format)."""
    try:
        content = path.read_text().strip()
        if not content:
            return None
        if content.startswith('{'):
            import json as _json
            return _json.loads(content).get('session_id', '')
        return content
    except Exception:
        return None


def _lookup_session_by_id(cursor, session_id: str, require_active: bool = True) -> dict | None:
    """Query sessions table by ID. Returns dict or None."""
    if not session_id:
        return None
    if require_active:
        cursor.execute("""
            SELECT session_id, ai_id, start_time FROM sessions
            WHERE session_id = ? AND end_time IS NULL
        """, (session_id,))
    else:
        cursor.execute("""
            SELECT session_id, ai_id, start_time FROM sessions
            WHERE session_id = ?
        """, (session_id,))
    row = cursor.fetchone()
    return dict(row) if row else None


def _search_session_files(cursor, start_dir: Path, filename: str) -> dict | None:
    """Search upward from start_dir for a session file, query DB if found."""
    for parent in [start_dir] + list(start_dir.parents):
        candidate = parent / '.empirica' / filename
        if candidate.exists():
            session_id = _read_session_file(candidate)
            result = _lookup_session_by_id(cursor, session_id)
            if result:
                return result
            break  # Found file but session ended
        if parent == Path.home() or parent == parent.parent:
            break
    return None


def get_active_session(db: SessionDatabase, ai_id: str, stdin_claude_session_id: str | None = None) -> dict | None:
    """
    Get the active session with strict pane isolation.

    Priority:
    0. Claude session_id (from stdin or TTY) → active_work file → empirica_session_id
    1. Instance-specific active_session file (active_session_tmux_N)
    2. Exact ai_id + exact instance_id match in DB (NO NULL fallback)
    3. Generic active_session file (only if no instance_id available)

    IMPORTANT: Never fall back to instance_id IS NULL - that causes
    cross-pane bleeding where any pane picks up legacy sessions.
    """
    cursor = db.conn.cursor()

    # Priority 0: instance_projects → empirica_session_id
    try:
        import json as _json

        from empirica.utils.session_resolver import InstanceResolver as R
        _gas_inst_id = R.instance_id()
        if _gas_inst_id:
            _gas_inst_file = Path.home() / '.empirica' / 'instance_projects' / f'{_gas_inst_id}.json'
            if _gas_inst_file.exists():
                with open(_gas_inst_file) as f:
                    _gas_session_id = _json.load(f).get('empirica_session_id')
                result = _lookup_session_by_id(cursor, _gas_session_id, require_active=False)
                if result:
                    return result
    except Exception:
        pass

    # Priority 1: Claude session_id → active_work → empirica_session_id
    try:
        import json as _json
        claude_session_id = _resolve_claude_session_id(stdin_claude_session_id)
        if claude_session_id:
            empirica_session_id = None
            active_work_path = Path.home() / '.empirica' / f'active_work_{claude_session_id}.json'
            if active_work_path.exists():
                with open(active_work_path) as f:
                    empirica_session_id = _json.load(f).get('empirica_session_id')
            if not empirica_session_id:
                try:
                    tty_session = R.tty_session(warn_if_stale=False)
                    if tty_session:
                        empirica_session_id = tty_session.get('empirica_session_id')
                except Exception:
                    pass
            result = _lookup_session_by_id(cursor, empirica_session_id, require_active=False)
            if result:
                return result
    except Exception:
        pass

    # Legacy: instance-specific active_session files
    try:
        current_instance_id = R.instance_id()
    except (ImportError, NameError):
        current_instance_id = None

    if current_instance_id:
        safe_instance = current_instance_id.replace(":", "_").replace("%", "")
        instance_suffix = f"_{safe_instance}"
        result = _search_session_files(cursor, Path.cwd(), f'active_session{instance_suffix}')
        if result:
            return result
        # Global instance file
        global_file = Path.home() / '.empirica' / f'active_session{instance_suffix}'
        if global_file.exists():
            session_id = _read_session_file(global_file)
            result = _lookup_session_by_id(cursor, session_id)
            if result:
                return result
    else:
        result = _search_session_files(cursor, Path.cwd(), 'active_session')
        if result:
            return result

    # Priority 2: Exact ai_id + STRICT instance_id match (no NULL fallback)
    if current_instance_id:
        cursor.execute("""
            SELECT session_id, ai_id, start_time
            FROM sessions
            WHERE end_time IS NULL AND ai_id = ?
              AND instance_id = ?
            ORDER BY start_time DESC
            LIMIT 1
        """, (ai_id, current_instance_id))
        row = cursor.fetchone()
        if row:
            return dict(row)
    else:
        # No instance isolation available - match any for this ai_id
        cursor.execute("""
            SELECT session_id, ai_id, start_time
            FROM sessions
            WHERE end_time IS NULL AND ai_id = ?
            ORDER BY start_time DESC
            LIMIT 1
        """, (ai_id,))
        row = cursor.fetchone()
        if row:
            return dict(row)

    return None


def get_latest_vectors(db: SessionDatabase, session_id: str, transaction_session_id: str | None = None, transaction_id: str | None = None) -> tuple:
    """
    Get latest vectors, phase, and gate decision from reflexes table.

    Args:
        db: Database connection
        session_id: Current session ID (for cache compatibility)
        transaction_session_id: The session ID where PREFLIGHT was submitted (survives compaction)
                               If provided, queries this session for vectors instead
        transaction_id: The specific transaction ID to filter by (for multi-instance isolation)
                       CRITICAL: Without this, shared sessions show wrong phase across instances

    This enables cross-session vector lookup when transactions span compaction boundaries.
    """
    cursor = db.conn.cursor()
    # Use transaction's session_id if available (survives compaction)
    lookup_session_id = transaction_session_id or session_id

    # CRITICAL: Filter by transaction_id to prevent cross-instance bleed
    # Without this, two Claudes sharing a session see each other's phases
    if transaction_id:
        cursor.execute("""
            SELECT phase, engagement, know, do, context,
                   clarity, coherence, signal, density,
                   state, change, completion, impact, uncertainty,
                   reflex_data
            FROM reflexes
            WHERE session_id = ? AND transaction_id = ?
            ORDER BY timestamp DESC
            LIMIT 1
        """, (lookup_session_id, transaction_id))
    else:
        cursor.execute("""
            SELECT phase, engagement, know, do, context,
                   clarity, coherence, signal, density,
                   state, change, completion, impact, uncertainty,
                   reflex_data
            FROM reflexes
            WHERE session_id = ?
            ORDER BY timestamp DESC
            LIMIT 1
        """, (lookup_session_id,))
    row = cursor.fetchone()

    if not row:
        return None, {}, None

    phase = row[0]
    vectors = {
        'engagement': row[1],
        'know': row[2],
        'do': row[3],
        'context': row[4],
        'clarity': row[5],
        'coherence': row[6],
        'signal': row[7],
        'density': row[8],
        'state': row[9],
        'change': row[10],
        'completion': row[11],
        'impact': row[12],
        'uncertainty': row[13],
    }

    # Filter out None values
    vectors = {k: v for k, v in vectors.items() if v is not None}

    # Extract gate decision from reflex_data (CHECK phase)
    gate_decision = None
    if row[14]:  # reflex_data column
        try:
            import json
            reflex_data = json.loads(row[14])
            gate_decision = reflex_data.get('decision')
        except Exception:
            pass

    return phase, vectors, gate_decision


def get_vector_deltas(db: SessionDatabase, session_id: str) -> dict:
    """
    Get learning deltas: PREFLIGHT → POSTFLIGHT only.

    This measures actual learning across the session, ignoring CHECK
    phases which are for gating, not learning measurement.
    """
    cursor = db.conn.cursor()

    # Get PREFLIGHT baseline (first PREFLIGHT in session)
    cursor.execute("""
        SELECT know, uncertainty, context, completion, engagement
        FROM reflexes
        WHERE session_id = ? AND phase = 'PREFLIGHT'
        ORDER BY timestamp ASC
        LIMIT 1
    """, (session_id,))
    preflight = cursor.fetchone()

    # Get latest POSTFLIGHT (final state)
    cursor.execute("""
        SELECT know, uncertainty, context, completion, engagement
        FROM reflexes
        WHERE session_id = ? AND phase = 'POSTFLIGHT'
        ORDER BY timestamp DESC
        LIMIT 1
    """, (session_id,))
    postflight = cursor.fetchone()

    if not preflight or not postflight:
        # Fallback: if no complete cycle, show sequential delta
        cursor.execute("""
            SELECT know, uncertainty, context, completion, engagement
            FROM reflexes
            WHERE session_id = ?
            ORDER BY timestamp DESC
            LIMIT 2
        """, (session_id,))
        rows = cursor.fetchall()
        if len(rows) < 2:
            return {}
        postflight = rows[0]
        preflight = rows[1]

    deltas = {}
    keys = ['know', 'uncertainty', 'context', 'completion', 'engagement']

    for i, key in enumerate(keys):
        post_val = postflight[i]
        pre_val = preflight[i]

        if post_val is not None and pre_val is not None:
            delta = post_val - pre_val
            if abs(delta) >= 0.05:  # Only show meaningful changes
                deltas[key] = delta

    return deltas


def format_deltas(deltas: dict) -> str:
    """Format deltas as a single summary symbol to prevent statusline overflow.

    Returns: green ✓ (net positive), red ⚠ (net negative), or white △ (neutral).
    """
    if not deltas:
        return ""

    # Calculate net direction across all vectors
    # For uncertainty, invert sign (lower uncertainty = improvement)
    net = 0.0
    for key, delta in deltas.items():
        if key == 'uncertainty':
            net -= delta  # Lower uncertainty is positive
        else:
            net += delta

    if net > 0.05:
        return f"{Colors.GREEN}✓{Colors.RESET}"
    elif net < -0.05:
        return f"{Colors.RED}⚠{Colors.RESET}"
    else:
        return f"{Colors.WHITE}△{Colors.RESET}"


def format_context_window(stdin_context: dict) -> str:
    """Format context window usage from Claude Code stdin data.

    Also writes usage to state file for UserPromptSubmit hook to read
    (hooks don't receive context_window, only statusline does).
    """
    ctx = stdin_context.get('context_window', {})
    used_pct = ctx.get('used_percentage', 0)
    if not used_pct:
        return ""

    # Write state file for hooks to read
    try:
        state_file = Path.home() / '.empirica' / 'context_usage.json'
        import json as _json_ctx
        import time as _time
        state_file.write_text(_json_ctx.dumps({
            'used_percentage': used_pct,
            'timestamp': _time.time(),
        }))
    except Exception:
        pass

    # Color: green < 50%, yellow 50-80%, red > 80%
    if used_pct >= 80:
        color = Colors.RED
    elif used_pct >= 50:
        color = Colors.YELLOW
    else:
        color = Colors.GREEN
    return f"{color}{int(used_pct)}%ctx{Colors.RESET}"


def format_statusline(
    session: dict,
    phase: str,
    vectors: dict,
    deltas: dict | None = None,
    mode: str = 'default',
    gate_decision: str | None = None,
    goal: dict | None = None,
    open_counts: dict | None = None,
    project_name: str | None = None,
    threshold_info: tuple | None = None,
    stdin_context: dict | None = None,
) -> str:
    """Format the statusline based on mode."""

    # Calculate confidence score
    confidence = calculate_confidence(vectors)
    conf_str = format_confidence(confidence)

    # Show project name instead of generic "empirica" branding
    # Truncate long names to keep statusline compact
    label = project_name or 'empirica'
    if len(label) > 20:
        label = label[:18] + '..'

    # Threshold indicator (user-facing only — AI doesn't see this)
    threshold_str = ""
    if threshold_info:
        know_t, unc_t, t_color = threshold_info
        threshold_str = f" {format_threshold(know_t, t_color)}"

    parts = [f"{Colors.GREEN}[{label}]{Colors.RESET} {conf_str}{threshold_str}"]

    # Add extension indicators from statusline_ext/*.json (replaces hardcoded CRM/WS)
    ext_str = read_statusline_extensions()
    if ext_str:
        parts.append(ext_str)

    if mode == 'basic':
        # Just confidence + threshold
        return ' '.join(parts)

    elif mode == 'default':
        # Redesigned: confidence ↕threshold │ 🎯goals ❓unknowns │ PHASE work_state% │ K:% C:%
        counts_str = format_open_counts(open_counts)
        parts.append(counts_str)

        # Phase + work state (investigating/acting with composite %)
        if phase:
            work_phase = determine_work_phase(phase, gate_decision)
            # CHECK uses readiness vectors, not execution vectors
            composite_phase = 'check' if phase == 'CHECK' else work_phase
            composite = calculate_phase_composite(vectors, composite_phase)
            phase_str = format_phase_state(phase, work_phase, composite, gate_decision)
            parts.append(phase_str)

        # K and C vectors (color-coded independently)
        if vectors:
            know = vectors.get('know', 0.0)
            context = vectors.get('context', 0.0)
            vec_parts = []
            vec_parts.append(format_vector_colored('K', know))
            vec_parts.append(format_vector_colored('C', context))
            parts.append(' '.join(vec_parts))

        # Add deltas only on POSTFLIGHT (deltas measure PREFLIGHT→POSTFLIGHT change)
        if phase == 'POSTFLIGHT' and deltas:
            delta_str = format_deltas(deltas)
            if delta_str:
                parts.append(f"Δ {delta_str}")

        # Context window usage (from Claude Code stdin)
        if stdin_context:
            ctx_str = format_context_window(stdin_context)
            if ctx_str:
                parts.append(ctx_str)

        return ' │ '.join(parts)

    elif mode == 'learning':
        # Focus on vectors with values and deltas (for developers)
        counts_str = format_open_counts(open_counts)
        parts.append(counts_str)

        if phase:
            parts.append(f"{phase}")

        if vectors:
            # Show more vectors with percentages
            all_keys = ['know', 'uncertainty', 'context', 'clarity', 'completion']
            vec_str = format_vectors_compact(vectors, keys=all_keys, use_percentage=True)
            parts.append(vec_str)

        # Show deltas only on POSTFLIGHT (deltas measure PREFLIGHT→POSTFLIGHT change)
        if phase == 'POSTFLIGHT' and deltas:
            delta_str = format_deltas(deltas)
            if delta_str:
                parts.append(f"Δ {delta_str}")

        return ' │ '.join(parts)

    else:  # full
        # Everything (for developers/debugging)
        ai_id = session.get('ai_id', 'unknown')
        session_id = session.get('session_id', '????')[:4]
        parts = [f"{Colors.BRIGHT_CYAN}[{label}:{ai_id}@{session_id}]{Colors.RESET}"]

        # Goal progress with more detail
        if goal:
            completed, total = goal.get('subtask_progress', (0, 0))
            goal_str = format_goal_progress(goal)
            if total > 0:
                goal_str += f" ({completed}/{total})"
            parts.append(goal_str)
        else:
            parts.append(f"{Colors.GRAY}no goal{Colors.RESET}")

        if phase:
            parts.append(f"{Colors.BLUE}{phase}{Colors.RESET}")

        if vectors:
            all_keys = ['know', 'uncertainty', 'context', 'clarity', 'engagement', 'completion', 'impact']
            vec_str = format_vectors_compact(vectors, keys=all_keys, use_percentage=True)
            parts.append(vec_str)

        # Show deltas only on POSTFLIGHT (deltas measure PREFLIGHT→POSTFLIGHT change)
        if phase == 'POSTFLIGHT' and deltas:
            delta_str = format_deltas(deltas)
            if delta_str:
                parts.append(f"Δ {delta_str}")

        return ' │ '.join(parts)


def build_statusline_data(
    session: dict,
    phase: str,
    vectors: dict,
    deltas: dict | None = None,
    gate_decision: str | None = None,
    goal: dict | None = None,
    open_counts: dict | None = None,
    project_name: str | None = None,
    project_path: str | None = None,
    ai_id: str | None = None,
) -> dict:
    """
    Build structured statusline data for JSON output.

    This enables TUI/GUI dashboards to consume statusline state.
    Extensions are read from ~/.empirica/statusline_ext/*.json (the statusline_ext protocol).

    Returns:
        {
            'project': {'name': str, 'path': str},
            'session': {'id': str, 'ai_id': str},
            'epistemic': {'phase': str, 'vectors': dict, 'deltas': dict, 'confidence': float},
            'goals': {'open': int, 'completion': float},
            'unknowns': {'open': int, 'blockers': int},
            'gate': {'decision': str},
            'extensions': [{'label': str, ...}, ...],
            'timestamp': float,
        }
    """
    import time

    confidence = calculate_confidence(vectors) if vectors else 0.0

    # Read extensions from statusline_ext JSON protocol
    extensions = _read_statusline_extensions_data()

    return {
        'project': {
            'name': project_name,
            'path': project_path,
        },
        'session': {
            'id': session.get('session_id') if session else None,
            'ai_id': ai_id or (session.get('ai_id') if session else None),
        },
        'epistemic': {
            'phase': phase,
            'vectors': vectors or {},
            'deltas': deltas or {},
            'confidence': confidence,
        },
        'goals': {
            'open': open_counts.get('open_goals', 0) if open_counts else 0,
            'active': goal.get('objective') if goal else None,
            'completion': open_counts.get('completion', 0.0) if open_counts else 0.0,
        },
        'unknowns': {
            'open': open_counts.get('open_unknowns', 0) if open_counts else 0,
            'blockers': open_counts.get('goal_linked_unknowns', 0) if open_counts else 0,
        },
        'gate': {
            'decision': gate_decision,
        },
        'extensions': extensions,
        'timestamp': time.time(),
    }


def format_tmux_statusline(confidence: float, phase: str) -> str:
    """
    Format a super-compact statusline for tmux status-right.

    Target: ~20 characters max for tmux status bar
    Format: "E:💡63% PRE"
    """
    pct = int(confidence * 100) if confidence else 0

    # Confidence emoji (no ANSI colors for tmux)
    if confidence >= 0.75:
        emoji = "⚡"
    elif confidence >= 0.50:
        emoji = "💡"
    elif confidence >= 0.35:
        emoji = "💫"
    else:
        emoji = "🌑"

    # Phase abbreviation
    phase_abbrev = {
        'PREFLIGHT': 'PRE',
        'CHECK': 'CHK',
        'POSTFLIGHT': 'POST',
        'INVESTIGATE': 'INV',
    }.get(phase, phase[:3] if phase else '---')

    return f"E:{emoji}{pct}% {phase_abbrev}"


def main():
    """Main statusline generation."""
    try:
        mode = os.getenv('EMPIRICA_STATUS_MODE', 'default').lower()
        output_json = '--json' in sys.argv or os.getenv('EMPIRICA_STATUS_JSON', '').lower() == 'true'
        output_tmux = '--tmux' in sys.argv or os.getenv('EMPIRICA_STATUS_TMUX', '').lower() == 'true'
        ai_id = get_ai_id()

        # HEADLESS CHECK: No statusline in headless/containerized mode
        try:
            from empirica.utils.session_resolver import InstanceResolver as R
            if R.is_headless():
                return  # Silent exit — no statusline output
        except ImportError:
            pass

        # OFF-RECORD CHECK: If Empirica is paused, show collapsed statusline
        pause_file = Path.home() / '.empirica' / 'sentinel_paused'
        if pause_file.exists():
            try:
                import json as _json
                import time as _time
                pause_data = _json.loads(pause_file.read_text())
                paused_at = pause_data.get('paused_at', 0)
                gap_minutes = int((_time.time() - paused_at) / 60) if paused_at else 0
                gap_str = f"{gap_minutes}m" if gap_minutes < 60 else f"{gap_minutes // 60}h{gap_minutes % 60}m"
                print(f"{Colors.GRAY}[empirica]{Colors.RESET} {Colors.YELLOW}OFF-RECORD{Colors.RESET} {Colors.GRAY}({gap_str}){Colors.RESET}")
            except Exception:
                print(f"{Colors.GRAY}[empirica]{Colors.RESET} {Colors.YELLOW}OFF-RECORD{Colors.RESET}")
            return

        # Read Claude Code stdin context (JSON with session_id, cwd, workspace, etc.)
        # This is the primary session resolution method in non-tmux environments
        stdin_context = {}
        stdin_claude_session_id = None
        try:
            import json as _json
            import select
            if not sys.stdin.isatty():
                ready, _, _ = select.select([sys.stdin], [], [], 0.1)
                if ready:
                    raw = sys.stdin.read()
                    if raw and raw.strip():
                        stdin_context = _json.loads(raw.strip())
                        stdin_claude_session_id = stdin_context.get('session_id')
        except Exception:
            pass

        # Auto-detect project from active context (6-tier priority chain)
        project_path = _resolve_project_path(stdin_claude_session_id)

        if project_path:
            db_path = Path(project_path) / '.empirica' / 'sessions' / 'sessions.db'
            db = SessionDatabase(db_path=str(db_path))
        else:
            # No local .empirica/ found - show "no project" instead of using global data
            # This prevents showing Empirica project data in unrelated projects
            print(f"{Colors.GRAY}[no project]{Colors.RESET}")
            return

        session = get_active_session(db, ai_id, stdin_claude_session_id=stdin_claude_session_id)

        # Get project_id and project_name from session for filtering and display
        project_id = None
        project_name = None
        if session:
            cursor = db.conn.cursor()
            cursor.execute("SELECT project_id FROM sessions WHERE session_id = ?", (session['session_id'],))
            row = cursor.fetchone()
            if row and row[0]:
                project_id = row[0]
                cursor.execute("SELECT name FROM projects WHERE id = ?", (project_id,))
                prow = cursor.fetchone()
                if prow:
                    project_name = prow[0]

        # If no session yet, still try to get project name from the most recent session
        if not project_name:
            cursor = db.conn.cursor()
            cursor.execute("""
                SELECT p.name FROM projects p
                JOIN sessions s ON s.project_id = p.id
                ORDER BY s.start_time DESC LIMIT 1
            """)
            prow = cursor.fetchone()
            if prow:
                project_name = prow[0]

        if not session:
            # No active session - show project name so user knows which pane this is
            label = project_name or ai_id
            if len(label) > 20:
                label = label[:18] + '..'
            print(f"{Colors.GRAY}[{label}:inactive]{Colors.RESET}")
            db.close()
            return

        session_id = session['session_id']

        # TRANSACTION AWARENESS: Read instance-specific active_transaction file
        # IMPORTANT: Uses instance suffix for multi-instance isolation (tmux panes)
        # The file is named active_transaction_{suffix}.json where suffix is sanitized (: → _)
        transaction_session_id = None
        transaction_id = None
        try:
            from empirica.utils.session_resolver import InstanceResolver as R
            # Build instance-aware filename (sanitized for non-tmux like x11:N → x11_N)
            suffix = R.instance_suffix()
            if project_path:
                tx_path = Path(project_path) / '.empirica' / f'active_transaction{suffix}.json'
            else:
                tx_path = Path.home() / '.empirica' / f'active_transaction{suffix}.json'
            if tx_path and tx_path.exists():
                import json as _json
                with open(tx_path) as f:
                    tx_data = _json.load(f)
                # Only use transaction data if status is "open" (active transaction)
                # If closed, fall back to current session_id
                if tx_data.get('status') == 'open':
                    transaction_session_id = tx_data.get('session_id')
                    transaction_id = tx_data.get('transaction_id')  # CRITICAL for instance isolation
        except Exception:
            pass  # Fall back to current session_id

        # Get vectors from DB (real-time) - use transaction's session_id and transaction_id
        # transaction_id is CRITICAL to prevent cross-instance phase bleed during active work
        # When no open transaction, fall back to session-level query to show last POSTFLIGHT state
        if transaction_id:
            phase, vectors, gate_decision = get_latest_vectors(db, session_id, transaction_session_id, transaction_id)
        else:
            # No open transaction — show last known state (typically POSTFLIGHT vectors)
            phase, vectors, gate_decision = get_latest_vectors(db, session_id)

        # Get deltas (learning measurement) - use transaction's session for continuity
        deltas = get_vector_deltas(db, transaction_session_id or session_id)

        # Get active goal for this session (used in 'full' mode)
        goal = get_active_goal(db, session_id)

        # Get open counts (goals/unknowns to close) - used in default/learning modes
        # Pass project_id to filter by THIS project only
        open_counts = get_open_counts(db, session_id, project_id=project_id)

        # Get dynamic threshold for statusline display (user-facing only)
        threshold_info = get_dynamic_threshold(db)

        db.close()

        # JSON output for dashboards
        if output_json:
            import json
            data = build_statusline_data(
                session, phase, vectors, deltas,
                gate_decision=gate_decision, goal=goal, open_counts=open_counts,
                project_name=project_name, project_path=project_path,
                ai_id=ai_id,
            )
            print(json.dumps(data, indent=2))
            return

        # Compact tmux output (for tmux status-right)
        if output_tmux:
            confidence = calculate_confidence(vectors) if vectors else 0.0
            print(format_tmux_statusline(confidence, phase))
            return

        # Format and output
        output = format_statusline(
            session, phase, vectors, deltas, mode,
            gate_decision=gate_decision, goal=goal, open_counts=open_counts,
            project_name=project_name, threshold_info=threshold_info,
            stdin_context=stdin_context,
        )
        print(output)

    except Exception as e:
        print(f"{Colors.GRAY}[empirica:error]{Colors.RESET}")
        # Log error
        try:
            from empirica.config.path_resolver import get_empirica_root
            with open(get_empirica_root() / 'statusline.log', 'a') as f:
                f.write(f"ERROR: {e}\n")
        except Exception:
            pass


if __name__ == '__main__':
    main()
