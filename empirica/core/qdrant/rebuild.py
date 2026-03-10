"""
Qdrant rebuild from SQLite — rebuild all collections from persistent DB state.

Used by `empirica rebuild --qdrant` to restore Qdrant after:
- Model/dimension change (e.g., nomic-embed-text → qwen3-embedding)
- Qdrant data loss or fresh deployment
- Collection corruption

Iterates all workspace projects, recreates collections at current dimensions,
and re-embeds all artifacts from each project's sessions.db.
"""

import hashlib
import logging
import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def _get_all_projects() -> List[Dict]:
    """Get all active projects from workspace.db."""
    workspace_db = Path.home() / '.empirica' / 'workspace' / 'workspace.db'
    if not workspace_db.exists():
        return []

    try:
        conn = sqlite3.connect(str(workspace_db))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, name, trajectory_path
            FROM global_projects
            WHERE status = 'active'
        """)
        projects = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return projects
    except Exception as e:
        logger.warning(f"Failed to read workspace.db: {e}")
        return []


def _embed_project_from_db(project_id: str, db_path: str, project_root: str) -> Dict[str, Any]:
    """Re-embed all artifacts for a project from its sessions.db into Qdrant.

    This is the core embed logic extracted for reuse by both project-embed
    and rebuild_qdrant_from_db.

    Returns dict with counts of embedded items per type.
    """
    from empirica.core.qdrant.connection import _check_qdrant_available
    from empirica.core.qdrant.memory import upsert_memory
    from empirica.core.qdrant.eidetic import embed_eidetic
    from empirica.data.session_database import SessionDatabase

    if not _check_qdrant_available():
        return {'error': 'Qdrant not available'}

    db = SessionDatabase(db_path=db_path)
    counts = {
        'findings': 0, 'unknowns': 0, 'mistakes': 0,
        'dead_ends': 0, 'lessons': 0, 'snapshots': 0,
        'eidetic': 0, 'code_api': 0, 'memory_total': 0,
    }

    try:
        # Gather all artifacts from SQLite
        findings = db.get_project_findings(project_id)
        unknowns = db.get_project_unknowns(project_id)

        cur = db.conn.cursor()

        # Mistakes
        cur.execute("""
            SELECT m.id, m.mistake, m.prevention, m.session_id
            FROM mistakes_made m
            JOIN sessions s ON m.session_id = s.session_id
            WHERE s.project_id = ?
            ORDER BY m.created_timestamp DESC
        """, (project_id,))
        mistakes = [dict(row) for row in cur.fetchall()]

        # Dead ends
        cur.execute("""
            SELECT id, approach, why_failed, session_id, goal_id, subtask_id, created_timestamp
            FROM project_dead_ends
            WHERE project_id = ?
            ORDER BY created_timestamp DESC
        """, (project_id,))
        dead_ends = [dict(row) for row in cur.fetchall()]

        # Lessons
        cur.execute("""
            SELECT id, name, description, domain, tags, lesson_data, created_timestamp
            FROM lessons
            ORDER BY created_timestamp DESC
        """)
        lessons = [dict(row) for row in cur.fetchall()]

        # Epistemic snapshots (episodic memory)
        cur.execute("""
            SELECT snapshot_id, session_id, context_summary, timestamp
            FROM epistemic_snapshots
            WHERE session_id IN (SELECT session_id FROM sessions WHERE project_id = ?)
            ORDER BY timestamp DESC
        """, (project_id,))
        snapshots = [dict(row) for row in cur.fetchall()]

        db.close()

        # Build memory items for upsert
        mem_items: List[Dict] = []
        mid = 1_000_000

        for f in findings:
            mem_items.append({
                'id': mid, 'text': f.get('finding', ''), 'type': 'finding',
                'goal_id': f.get('goal_id'), 'subtask_id': f.get('subtask_id'),
                'session_id': f.get('session_id'), 'timestamp': f.get('created_timestamp'),
                'subject': f.get('subject'),
            })
            mid += 1

        for u in unknowns:
            mem_items.append({
                'id': mid, 'text': u.get('unknown', ''), 'type': 'unknown',
                'goal_id': u.get('goal_id'), 'subtask_id': u.get('subtask_id'),
                'session_id': u.get('session_id'), 'timestamp': u.get('created_timestamp'),
                'subject': u.get('subject'), 'is_resolved': u.get('is_resolved', False),
            })
            mid += 1

        for m in mistakes:
            text = f"{m.get('mistake', '')} Prevention: {m.get('prevention', '')}"
            mem_items.append({
                'id': mid, 'text': text, 'type': 'mistake',
                'session_id': m.get('session_id'), 'timestamp': m.get('created_timestamp'),
            })
            mid += 1

        for d in dead_ends:
            text = f"DEAD END: {d.get('approach', '')} Why failed: {d.get('why_failed', '')}"
            mem_items.append({
                'id': mid, 'text': text, 'type': 'dead_end',
                'session_id': d.get('session_id'), 'goal_id': d.get('goal_id'),
                'subtask_id': d.get('subtask_id'), 'timestamp': d.get('created_timestamp'),
            })
            mid += 1

        for lesson in lessons:
            text = f"LESSON: {lesson.get('name', '')} - {lesson.get('description', '')} Domain: {lesson.get('domain', '')}"
            mem_items.append({
                'id': mid, 'text': text, 'type': 'lesson',
                'lesson_id': lesson.get('id'), 'domain': lesson.get('domain'),
                'tags': lesson.get('tags'), 'timestamp': lesson.get('created_timestamp'),
            })
            mid += 1

        for snap in snapshots:
            context = snap.get('context_summary', '')
            if context:
                text = f"SESSION NARRATIVE: {context}"
                mem_items.append({
                    'id': mid, 'text': text, 'type': 'episodic',
                    'session_id': snap.get('session_id'),
                    'snapshot_id': snap.get('snapshot_id'),
                    'timestamp': snap.get('timestamp'),
                })
                mid += 1

        upsert_memory(project_id, mem_items)

        counts['findings'] = len(findings)
        counts['unknowns'] = len(unknowns)
        counts['mistakes'] = len(mistakes)
        counts['dead_ends'] = len(dead_ends)
        counts['lessons'] = len(lessons)
        counts['snapshots'] = len(snapshots)
        counts['memory_total'] = len(mem_items)

        # Eidetic rehydration from findings
        for f in findings:
            finding_text = f.get('finding', '')
            if not finding_text:
                continue
            content_hash = hashlib.md5(finding_text.encode()).hexdigest()
            impact = f.get('impact')
            base_confidence = float(impact) if impact else 0.6
            try:
                success = embed_eidetic(
                    project_id=project_id,
                    fact_id=f.get('id', content_hash),
                    content=finding_text,
                    fact_type="fact",
                    domain=f.get('subject'),
                    source_sessions=[f.get('session_id')] if f.get('session_id') else None,
                    source_findings=[f.get('id')] if f.get('id') else None,
                    confidence=base_confidence,
                    tags=[f.get('subject')] if f.get('subject') else None,
                )
                if success:
                    counts['eidetic'] += 1
            except Exception as e:
                logger.debug(f"Eidetic embed failed for finding {f.get('id', 'unknown')}: {e}")

        # Code API embedding
        try:
            from empirica.core.qdrant.code_embeddings import embed_project_code
            code_root = Path(project_root)
            if code_root.is_dir():
                code_result = embed_project_code(project_id, code_root)
                counts['code_api'] = code_result.get('modules_embedded', 0)
        except Exception as e:
            logger.debug(f"Code embedding skipped for {project_id}: {e}")

    except Exception as e:
        logger.error(f"Failed to embed project {project_id}: {e}")
        counts['error'] = str(e)

    return counts


def rebuild_qdrant_from_db() -> Dict:
    """Rebuild all Qdrant collections from SQLite for all workspace projects.

    Steps:
    1. Get all active projects from workspace.db
    2. For each project: recreate collections at current dimensions, re-embed from DB
    3. Recreate global collections

    Returns summary dict with per-project results.
    """
    from empirica.core.qdrant.connection import _check_qdrant_available
    from empirica.core.qdrant.collections import (
        recreate_project_collections,
        recreate_global_collections,
    )

    if not _check_qdrant_available():
        return {'ok': False, 'error': 'Qdrant not available'}

    projects = _get_all_projects()
    if not projects:
        return {'ok': False, 'error': 'No projects found in workspace.db'}

    results = {
        'ok': True,
        'projects': {},
        'global_collections': None,
        'total_projects': len(projects),
        'successful': 0,
        'failed': 0,
    }

    for project in projects:
        project_id = project['id']
        project_name = project.get('name', project_id)
        trajectory_path = project.get('trajectory_path', '')

        if not trajectory_path or not Path(trajectory_path).is_dir():
            results['projects'][project_name] = {'error': f'Path not found: {trajectory_path}'}
            results['failed'] += 1
            continue

        # Find sessions.db — trajectory_path may point to .empirica/ or project root
        if trajectory_path.endswith('.empirica'):
            db_path = os.path.join(trajectory_path, 'sessions', 'sessions.db')
            project_root = os.path.dirname(trajectory_path)
        else:
            db_path = os.path.join(trajectory_path, '.empirica', 'sessions', 'sessions.db')
            project_root = trajectory_path

        if not os.path.exists(db_path):
            results['projects'][project_name] = {'skipped': 'No sessions.db'}
            continue

        logger.info(f"Rebuilding Qdrant for project: {project_name} ({project_id})")

        # Step 1: Recreate collections with current dimensions
        try:
            recreate_result = recreate_project_collections(project_id)
        except Exception as e:
            results['projects'][project_name] = {'error': f'Collection recreate failed: {e}'}
            results['failed'] += 1
            continue

        # Step 2: Re-embed from DB
        embed_result = _embed_project_from_db(project_id, db_path, project_root)

        results['projects'][project_name] = {
            'collections': recreate_result,
            'embedded': embed_result,
        }

        if 'error' in embed_result:
            results['failed'] += 1
        else:
            results['successful'] += 1

    # Step 3: Recreate global collections
    try:
        results['global_collections'] = recreate_global_collections()
    except Exception as e:
        results['global_collections'] = {'error': str(e)}

    return results
