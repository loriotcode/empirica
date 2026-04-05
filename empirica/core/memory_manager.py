"""
Memory Manager — manages the CC memory/*.md KV cache layer.

5-tier memory hierarchy:
  L1: MEMORY.md index (~200 lines, always loaded)
  L2: memory/*.md files (loaded on relevance by CC)
  L3: Qdrant eidetic/episodic (loaded at PREFLIGHT)
  L4: sessions.db (queried on demand)
  L5: git notes (portable, cold storage)

This module manages L1 and L2, driven by the transaction cycle:
  POSTFLIGHT → update hot cache, promote high-value facts, demote stale files

Extracted from session-end-postflight.py for shared use by both
POSTFLIGHT handler (workflow_commands.py) and session-end hook.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Markers for auto-generated section in MEMORY.md
MEMORY_AUTO_START = "## EPISTEMIC FOCUS (Confidence-Ranked)"
MEMORY_AUTO_END = "---\n📊"
# Max lines for auto section (leaves room for manual notes within CC's 200 line cap)
MEMORY_AUTO_MAX_LINES = 100


def get_memory_dir(project_path: Optional[str] = None) -> Optional[Path]:
    """Resolve the CC memory directory for a project.

    CC stores memories at: ~/.claude/projects/{path-key}/memory/
    where path-key is the absolute path with / replaced by -

    Args:
        project_path: Explicit project path. If None, uses CWD and git root.

    Returns:
        Path to memory directory, or None if not found.
    """
    candidates = []

    if project_path:
        candidates.append(Path(project_path).resolve())

    # Try CWD
    try:
        candidates.append(Path.cwd().resolve())
    except Exception:
        pass

    # Try git root
    try:
        import subprocess
        result = subprocess.run(
            ['git', 'rev-parse', '--show-toplevel'],
            capture_output=True, text=True, timeout=2, check=False,
        )
        if result.returncode == 0:
            candidates.append(Path(result.stdout.strip()).resolve())
    except Exception:
        pass

    for candidate in candidates:
        project_key = str(candidate).replace('/', '-')
        memory_dir = Path.home() / '.claude' / 'projects' / project_key / 'memory'
        if memory_dir.exists():
            return memory_dir

    return None


def get_memory_md_path(project_path: Optional[str] = None) -> Optional[Path]:
    """Get the MEMORY.md path for the current project."""
    memory_dir = get_memory_dir(project_path)
    if memory_dir:
        return memory_dir / 'MEMORY.md'
    return None


def resolve_project_id(session_id: str, db_path: Optional[str] = None) -> Optional[str]:
    """Resolve project_id from session_id via DB lookup."""
    if not db_path:
        candidate = Path.cwd() / '.empirica' / 'sessions' / 'sessions.db'
        if not candidate.exists():
            candidate = Path.home() / '.empirica' / 'sessions' / 'sessions.db'
        if not candidate.exists():
            return None
        db_path = str(candidate)

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT project_id FROM sessions WHERE session_id = ?",
            (session_id,)
        )
        row = cursor.fetchone()
        conn.close()
        if row:
            return row[0]
    except Exception:
        pass
    return None


def fetch_ranked_artifacts(session_id: str, db_path: Optional[str] = None,
                           limit: int = 20) -> dict:
    """Fetch recent artifacts from DB, scoped to project.

    Returns dict with findings, unknowns, dead_ends, goals, mistakes.
    """
    result = {'findings': [], 'unknowns': [], 'dead_ends': [], 'goals': [], 'mistakes': []}

    if not db_path:
        candidate = Path.cwd() / '.empirica' / 'sessions' / 'sessions.db'
        if not candidate.exists():
            candidate = Path.home() / '.empirica' / 'sessions' / 'sessions.db'
        if not candidate.exists():
            return result
        db_path = str(candidate)

    try:
        project_id = resolve_project_id(session_id, db_path)
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Build project filter
        if project_id:
            pf = "WHERE project_id = ?"
            pf_args = (project_id,)
            uf = "WHERE is_resolved = 0 AND project_id = ?"
            uf_args = (project_id,)
            gf = "WHERE is_completed = 0 AND project_id = ?"
            gf_args = (project_id,)
        else:
            pf, pf_args = "", ()
            uf, uf_args = "WHERE is_resolved = 0", ()
            gf, gf_args = "WHERE is_completed = 0", ()

        # Findings
        cursor.execute(f"""
            SELECT finding, impact, created_timestamp FROM project_findings
            {pf} ORDER BY created_timestamp DESC LIMIT ?
        """, (*pf_args, limit))
        for row in cursor.fetchall():
            result['findings'].append({
                'finding': row[0], 'impact': row[1] or 0.5,
                'created_timestamp': row[2],
            })

        # Open unknowns
        cursor.execute(f"""
            SELECT unknown, created_timestamp FROM project_unknowns
            {uf} ORDER BY created_timestamp DESC LIMIT ?
        """, (*uf_args, 10))
        for row in cursor.fetchall():
            result['unknowns'].append({'unknown': row[0], 'created_timestamp': row[1]})

        # Dead-ends
        cursor.execute(f"""
            SELECT approach, why_failed, created_timestamp FROM project_dead_ends
            {pf} ORDER BY created_timestamp DESC LIMIT ?
        """, (*pf_args, 10))
        for row in cursor.fetchall():
            result['dead_ends'].append({
                'approach': row[0], 'why_failed': row[1],
                'created_timestamp': row[2],
            })

        # Open goals
        cursor.execute(f"""
            SELECT objective, status FROM project_goals
            {gf} ORDER BY created_timestamp DESC LIMIT ?
        """, (*gf_args, 10))
        for row in cursor.fetchall():
            result['goals'].append({'objective': row[0], 'status': row[1]})

        # Mistakes
        cursor.execute(f"""
            SELECT mistake, prevention, created_timestamp FROM project_mistakes
            {pf} ORDER BY created_timestamp DESC LIMIT ?
        """, (*pf_args, 5))
        for row in cursor.fetchall():
            result['mistakes'].append({
                'mistake': row[0], 'prevention': row[1],
                'created_timestamp': row[2],
            })

        conn.close()
    except Exception as e:
        logger.debug(f"fetch_ranked_artifacts failed: {e}")

    return result


def update_hot_cache(session_id: str, project_path: Optional[str] = None,
                     db_path: Optional[str] = None) -> bool:
    """Update MEMORY.md auto-generated section with ranked artifacts.

    Preserves manual content. Auto section is delimited by markers.
    Called at POSTFLIGHT (primary) and session-end (fallback).

    Returns True if MEMORY.md was updated.
    """
    memory_path = get_memory_md_path(project_path)
    if not memory_path:
        logger.debug("update_hot_cache: no memory path found")
        return False

    artifacts = fetch_ranked_artifacts(session_id, db_path)

    # Format the auto section
    auto_lines = []
    auto_lines.append(f"\n{MEMORY_AUTO_START}\n")

    # Rank findings by impact, include top items
    ranked = sorted(artifacts['findings'], key=lambda f: f.get('impact', 0.5), reverse=True)

    # Critical findings (impact > 0.7)
    critical = [f for f in ranked if f.get('impact', 0.5) > 0.7]
    if critical:
        auto_lines.append("### Critical (weight > 0.7)")
        for f in critical[:5]:
            impact = f.get('impact', 0.5)
            text = f['finding'][:100].replace('\n', ' ')
            auto_lines.append(f"- [{impact:.2f}] **Finding:** {text}...")

    # Important findings (impact 0.4-0.7)
    important = [f for f in ranked if 0.4 <= f.get('impact', 0.5) <= 0.7]
    if important:
        auto_lines.append("\n### Important (weight 0.4-0.7)")
        for f in important[:5]:
            impact = f.get('impact', 0.5)
            text = f['finding'][:100].replace('\n', ' ')
            auto_lines.append(f"- [{impact:.2f}] **Finding:** {text}...")

    # Dead-ends (always valuable)
    if artifacts['dead_ends']:
        auto_lines.append("\n### Dead-Ends (avoid re-trying)")
        for d in artifacts['dead_ends'][:3]:
            approach = d['approach'][:80].replace('\n', ' ')
            why = d['why_failed'][:60].replace('\n', ' ')
            auto_lines.append(f"- [{0.5 + len(d.get('why_failed', '')) / 500:.2f}] **Dead-End:** {approach}")

    # Mistakes with prevention
    if artifacts['mistakes']:
        auto_lines.append("\n### Mistakes (prevention strategies)")
        for m in artifacts['mistakes'][:3]:
            text = m['mistake'][:80].replace('\n', ' ')
            auto_lines.append(f"- [{0.50:.2f}] **Mistake:** {text}")

    # Open goals
    if artifacts['goals']:
        auto_lines.append(f"\n### Active Goals ({len(artifacts['goals'])})")
        for g in artifacts['goals'][:5]:
            obj = g['objective'][:80].replace('\n', ' ')
            auto_lines.append(f"- [{0.44:.2f}] **Goal:** {obj}...")

    # Footer with retrieval hints
    total_items = sum(len(v) for v in artifacts.values())
    auto_lines.append(f"\n---")
    auto_lines.append(f"📊 **{total_items} items ranked** | For deeper context:")
    auto_lines.append(f"- `empirica project-bootstrap --session-id {session_id[:8]}` (full load + subtasks)")
    auto_lines.append(f"- `empirica project-search --task \"<query>\"` (Qdrant semantic)")
    auto_lines.append(f"- `git notes show --ref=breadcrumbs HEAD` (session narrative)")

    auto_section = '\n'.join(auto_lines) + '\n'

    # Enforce line cap
    auto_section_lines = auto_section.count('\n')
    if auto_section_lines > MEMORY_AUTO_MAX_LINES:
        lines = auto_section.split('\n')
        auto_section = '\n'.join(lines[:MEMORY_AUTO_MAX_LINES]) + '\n'

    # Read existing MEMORY.md
    if memory_path.exists():
        existing = memory_path.read_text()
    else:
        existing = "# Empirica Project Memory\n"

    # Replace or append auto section
    if MEMORY_AUTO_START in existing:
        # Find the auto section and replace it
        start_idx = existing.index(MEMORY_AUTO_START)
        # Find the end marker (the ---\n📊 line and everything after it until next ## or end)
        end_marker = "📊 **"
        if end_marker in existing[start_idx:]:
            # Find the end of the auto section (next line after the 📊 footer)
            end_search = existing.index(end_marker, start_idx)
            # Go to end of line
            end_idx = existing.find('\n', end_search)
            if end_idx == -1:
                end_idx = len(existing)
            else:
                # Include any trailing retrieval hints (lines starting with -)
                while end_idx < len(existing) - 1:
                    next_line_end = existing.find('\n', end_idx + 1)
                    if next_line_end == -1:
                        next_line_end = len(existing)
                    next_line = existing[end_idx + 1:next_line_end]
                    if next_line.startswith('- `empirica') or next_line.startswith('- `git'):
                        end_idx = next_line_end
                    else:
                        break
                end_idx += 1  # Include the final newline
            updated = existing[:start_idx] + auto_section + existing[end_idx:]
        else:
            # No end marker found, replace to end of file
            updated = existing[:start_idx] + auto_section
    else:
        # Append at end
        updated = existing.rstrip('\n') + '\n' + auto_section

    # Write back
    try:
        memory_path.parent.mkdir(parents=True, exist_ok=True)
        memory_path.write_text(updated)
        logger.debug(f"Updated MEMORY.md hot cache ({auto_section_lines} lines)")
        return True
    except Exception as e:
        logger.warning(f"Failed to write MEMORY.md: {e}")
        return False


def get_memory_stats(project_path: Optional[str] = None) -> dict:
    """Get stats about the CC memory layer for memory-report.

    Returns:
        dict with file_count, total_size_bytes, memory_md_lines,
        oldest_file, newest_file, auto_section_lines
    """
    memory_dir = get_memory_dir(project_path)
    if not memory_dir:
        return {"error": "Memory directory not found"}

    md_files = list(memory_dir.glob('*.md'))
    stats = {
        "memory_dir": str(memory_dir),
        "file_count": len(md_files),
        "total_size_bytes": sum(f.stat().st_size for f in md_files),
        "files": [],
    }

    # MEMORY.md specific stats
    memory_md = memory_dir / 'MEMORY.md'
    if memory_md.exists():
        content = memory_md.read_text()
        stats["memory_md_lines"] = content.count('\n')
        stats["memory_md_has_auto_section"] = MEMORY_AUTO_START in content
        if MEMORY_AUTO_START in content:
            auto_start = content.index(MEMORY_AUTO_START)
            auto_content = content[auto_start:]
            stats["auto_section_lines"] = auto_content.count('\n')
    else:
        stats["memory_md_lines"] = 0

    # Individual file stats
    for f in sorted(md_files, key=lambda p: p.stat().st_mtime, reverse=True):
        if f.name == 'MEMORY.md':
            continue
        stats["files"].append({
            "name": f.name,
            "size": f.stat().st_size,
            "modified": f.stat().st_mtime,
        })

    return stats
