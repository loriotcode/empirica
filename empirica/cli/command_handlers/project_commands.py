"""
Project Commands - Multi-repo/multi-session project tracking
"""

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any, Optional

from empirica.utils.session_resolver import InstanceResolver as R

from ..cli_utils import handle_cli_error, run_empirica_subprocess

logger = logging.getLogger(__name__)


# ============================================================================
# WORKSPACE DATABASE ACCESS
# ============================================================================
# The workspace database (~/.empirica/workspace/workspace.db) contains the
# global_projects table with all projects across the ecosystem.
# This is separate from SessionDatabase which operates on local project data.
# ============================================================================

def get_workspace_db_path() -> Path:
    """Get path to workspace database"""
    return Path.home() / '.empirica' / 'workspace' / 'workspace.db'


def ensure_workspace_schema(conn) -> None:
    """Create workspace tables if they don't exist.

    Called before any workspace.db operations to ensure the schema is present.
    This makes workspace-init and project-list work on fresh installs without
    requiring a separate schema migration step.
    """
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS global_projects (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT,
            trajectory_path TEXT NOT NULL UNIQUE,
            git_remote_url TEXT,
            git_branch TEXT DEFAULT 'main',
            total_transactions INTEGER DEFAULT 0,
            total_findings INTEGER DEFAULT 0,
            total_unknowns INTEGER DEFAULT 0,
            total_dead_ends INTEGER DEFAULT 0,
            total_goals INTEGER DEFAULT 0,
            last_transaction_id TEXT,
            last_transaction_timestamp REAL,
            last_sync_timestamp REAL,
            status TEXT DEFAULT 'active',
            project_type TEXT DEFAULT 'product',
            project_tags TEXT,
            created_timestamp REAL NOT NULL,
            updated_timestamp REAL NOT NULL,
            metadata TEXT
        )
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_global_projects_status
        ON global_projects(status)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_global_projects_type
        ON global_projects(project_type)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_global_projects_last_tx
        ON global_projects(last_transaction_timestamp)
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS instance_bindings (
            instance_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            project_path TEXT,
            bound_timestamp REAL NOT NULL,
            FOREIGN KEY (project_id) REFERENCES global_projects(id)
        )
    """)
    # Global session registry — enables cross-project session tracking
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS global_sessions (
            session_id TEXT PRIMARY KEY,
            ai_id TEXT,
            origin_project_id TEXT,
            current_project_id TEXT,
            instance_id TEXT,
            status TEXT DEFAULT 'active',
            parent_session_id TEXT,
            created_at REAL,
            last_activity REAL,
            FOREIGN KEY (origin_project_id) REFERENCES global_projects(id),
            FOREIGN KEY (current_project_id) REFERENCES global_projects(id)
        )
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_global_sessions_instance
        ON global_sessions(instance_id, status)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_global_sessions_project
        ON global_sessions(current_project_id)
    """)
    # Entity-artifact cross-references — links artifacts to CRM entities
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS entity_artifacts (
            id TEXT PRIMARY KEY,
            artifact_type TEXT NOT NULL,
            artifact_id TEXT NOT NULL,
            artifact_source TEXT,
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            relationship TEXT DEFAULT 'about',
            relevance REAL DEFAULT 1.0,
            discovered_via TEXT,
            engagement_id TEXT,
            transaction_id TEXT,
            created_at REAL,
            created_by_ai TEXT,
            UNIQUE(artifact_type, artifact_id, entity_type, entity_id)
        )
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_entity_artifacts_entity
        ON entity_artifacts(entity_type, entity_id)
    """)
    conn.commit()


def get_workspace_projects() -> list[dict[str, Any]]:
    """
    Get all projects from workspace database.

    Returns list of project dicts with keys:
    - id, name, description, trajectory_path, status, project_type
    - total_transactions, total_findings, total_unknowns, etc.
    - folder_name (derived from trajectory_path)
    """
    workspace_db = get_workspace_db_path()
    if not workspace_db.exists():
        return []

    try:
        conn = sqlite3.connect(str(workspace_db))
        ensure_workspace_schema(conn)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                id, name, description, trajectory_path, git_remote_url,
                git_branch, total_transactions, total_findings,
                total_unknowns, total_dead_ends, total_goals,
                last_transaction_id, last_transaction_timestamp,
                last_sync_timestamp, status, project_type, project_tags,
                created_timestamp, updated_timestamp
            FROM global_projects
            WHERE status = 'active'
            ORDER BY updated_timestamp DESC
        """)

        projects = []
        for row in cursor.fetchall():
            project = dict(row)
            # Derive folder name from trajectory_path
            # e.g., /home/user/empirical-ai/empirica-platform/.empirica -> empirica-platform
            traj_path = project.get('trajectory_path', '')
            if traj_path:
                folder_name = Path(traj_path).parent.name
                project['folder_name'] = folder_name
            else:
                project['folder_name'] = project.get('name', '')
            projects.append(project)

        conn.close()
        return projects
    except Exception as e:
        logger.warning(f"Failed to read workspace database: {e}")
        return []



def _update_active_work(project_path: str, folder_name: str, empirica_session_id: str = None, claude_session_id: str = None) -> bool:
    """
    Update active_work markers AND TTY session for cross-project continuity.

    This is called by project-switch to record which project is currently active.
    Updates:
    1. TTY session file's project_path - PRIMARY source for hooks/statusline
    2. Session-specific active_work file (if TTY session exists) - backup
    3. Canonical active_work file - fallback for first-time compaction

    The TTY session file is the source of truth for "what project is this
    Claude instance working on". Hooks and statusline read it to determine
    the correct project context.

    Args:
        project_path: Full path to the project directory
        folder_name: The project's folder name (ground truth identifier)
        empirica_session_id: Optional session ID to attach to (for Sentinel/MCP tools)
        claude_session_id: Claude Code conversation UUID (for instance isolation when called via Bash tool)

    Returns:
        True if successfully written, False otherwise
    """
    import time

    try:
        marker_dir = Path.home() / '.empirica'
        marker_dir.mkdir(parents=True, exist_ok=True)

        # Try to get Claude session ID - prefer explicit parameter over TTY session
        # claude_session_id parameter is set when called from project-switch with --claude-session-id
        tty_session = R.tty_session()
        if not claude_session_id:
            claude_session_id = tty_session.get('claude_session_id') if tty_session else None

        # CRITICAL: Update instance_projects and TTY session with new project_path
        # instance_projects is used by Sentinel and statusline when running via Bash tool
        # TTY session is used for direct terminal context
        tty_key = R.tty_key()

        # Use canonical get_instance_id() which supports tmux, X11, macOS Terminal, TTY
        # Previously only checked TMUX_PANE, breaking X11 and other non-tmux environments
        instance_id = R.instance_id()

        # When instance_id is absent (Bash tool subprocess may lack env vars),
        # resolve from claude_session_id by scanning instance_projects/ files.
        # Hooks write claude_session_id to instance_projects at session start —
        # this reverse-lookup finds the correct instance.
        if not instance_id and claude_session_id:
            instance_dir = marker_dir / 'instance_projects'
            if instance_dir.exists():
                for ip_file in instance_dir.glob('*.json'):
                    try:
                        with open(ip_file) as f:
                            ip_data = json.load(f)
                        if ip_data.get('claude_session_id') == claude_session_id:
                            instance_id = ip_file.stem  # e.g. "tmux_14", "x11_77663748"
                            logger.debug(f"Resolved instance_id={instance_id} from claude_session_id match in {ip_file.name}")
                            break
                    except Exception:
                        continue

        # Fallback 3: Read instance_id from TTY session file.
        # The TTY session stores instance_id from a prior hook that had env vars.
        # This handles the case where Bash tool has no env AND claude_session_id
        # is null (e.g., session-create doesn't write claude_session_id to TTY file).
        if not instance_id and tty_session:
            instance_id = tty_session.get('instance_id')
            if instance_id:
                logger.debug(f"Resolved instance_id={instance_id} from TTY session file")

        # PRESERVE existing claude_session_id if TTY session doesn't have it
        # This handles the case where session-init hook ran and wrote claude_session_id
        # to instance_projects, but TTY session wasn't updated (e.g., Bash tool context)
        if not claude_session_id and instance_id:
            existing_instance_file = marker_dir / 'instance_projects' / f'{instance_id}.json'
            if existing_instance_file.exists():
                try:
                    with open(existing_instance_file) as f:
                        existing_data = json.load(f)
                        claude_session_id = existing_data.get('claude_session_id')
                        logger.debug(f"Preserved claude_session_id from existing instance_projects: {claude_session_id}")
                except Exception:
                    pass

        # Reverse-lookup: if we have empirica_session_id but not claude_session_id,
        # scan active_work_*.json files for one matching our empirica_session_id.
        # session-init wrote this file with both IDs — we can discover claude_session_id
        # from existing state without needing it passed as a flag.
        if not claude_session_id and empirica_session_id:
            for aw_file in marker_dir.glob('active_work_*.json'):
                try:
                    with open(aw_file) as f:
                        aw_data = json.load(f)
                    if aw_data.get('empirica_session_id') == empirica_session_id:
                        claude_session_id = aw_file.stem.replace('active_work_', '')
                        logger.debug(f"Resolved claude_session_id={claude_session_id[:12]} from active_work reverse-lookup")
                        break
                except Exception:
                    continue

        if not claude_session_id and instance_id:
            logger.debug(
                f"claude_session_id unknown for {instance_id}. "
                f"Will update generic active_work.json only."
            )

        # Write instance_projects FIRST - works via Bash tool where tty fails
        if instance_id:
            instance_dir = marker_dir / 'instance_projects'
            instance_dir.mkdir(parents=True, exist_ok=True)
            instance_file = instance_dir / f'{instance_id}.json'
            instance_data = {
                'project_path': project_path,
                'tty_key': tty_key,  # May be None if via Bash tool
                'claude_session_id': claude_session_id,
                'empirica_session_id': empirica_session_id,
                'timestamp': time.strftime('%Y-%m-%dT%H:%M:%S%z')
            }
            with open(instance_file, 'w') as f:
                json.dump(instance_data, f, indent=2)
            logger.debug(f"Updated instance_projects: {instance_id} -> {folder_name}")

        # Write TTY session if available (direct terminal context)
        if tty_key:
            tty_sessions_dir = marker_dir / 'tty_sessions'
            tty_sessions_dir.mkdir(parents=True, exist_ok=True)
            tty_session_file = tty_sessions_dir / f'{tty_key}.json'

            # Read existing TTY session, update project_path, write back
            tty_data = {}
            if tty_session_file.exists():
                try:
                    with open(tty_session_file) as f:
                        tty_data = json.load(f)
                except Exception:
                    pass

            # Update with new project path
            tty_data['project_path'] = project_path
            tty_data['tty_key'] = tty_key
            tty_data['instance_id'] = instance_id
            tty_data['timestamp'] = time.strftime('%Y-%m-%dT%H:%M:%S%z')

            with open(tty_session_file, 'w') as f:
                json.dump(tty_data, f, indent=2)
            logger.debug(f"Updated TTY session project_path: {folder_name}")

        # Update active_session file (used by statusline for session+project lookup)
        # session-create writes this initially but project-switch must update it
        # when the project changes, otherwise statusline reads from wrong project DB.
        try:
            as_suffix = R.instance_suffix()
            if as_suffix:
                as_file = marker_dir / f'active_session{as_suffix}'
                as_data = {
                    'session_id': empirica_session_id,
                    'project_path': project_path,
                    'ai_id': 'claude-code',
                }
                with open(as_file, 'w') as f:
                    json.dump(as_data, f)
                import os as _os
                _os.chmod(as_file, 0o600)
                logger.debug(f"Updated active_session{as_suffix}: {folder_name}")
        except Exception as e:
            logger.debug(f"Could not update active_session: {e}")

        active_work = {
            'project_path': project_path,
            'folder_name': folder_name,
            'claude_session_id': claude_session_id,
            'empirica_session_id': empirica_session_id,  # For Sentinel/MCP session attachment
            'source': 'project-switch',
            'timestamp': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
            'timestamp_epoch': time.time()
        }

        # Also write to session-specific file (if we know the session)
        # This prevents race conditions with other Claude instances
        if claude_session_id:
            session_marker_path = marker_dir / f'active_work_{claude_session_id}.json'
            with open(session_marker_path, 'w') as f:
                json.dump(active_work, f, indent=2)
            logger.debug(f"Updated session-specific active_work: {folder_name}")

        # Write generic active_work.json in headless mode only.
        # In interactive mode, instance_projects + active_work_{uuid} handle everything.
        # The generic file would just pollute resolution with stale cross-terminal data.
        try:
            _headless = R.is_headless()
        except ImportError:
            _headless = True  # Conservative: write if can't detect

        if _headless:
            marker_path = marker_dir / 'active_work.json'
            with open(marker_path, 'w') as f:
                json.dump(active_work, f, indent=2)
            logger.debug(f"Updated active_work.json (headless): {folder_name} -> {project_path}")
        return True

    except Exception as e:
        logger.warning(f"Failed to update active_work.json: {e}")
        return False


def resolve_workspace_project(identifier: str) -> dict[str, Any] | None:
    """
    Resolve a project by name, folder name, or UUID from workspace database.

    Args:
        identifier: Project name, folder name (repo directory name), or UUID

    Returns:
        Project dict or None if not found
    """
    projects = get_workspace_projects()

    # Try exact UUID match first
    for p in projects:
        if p.get('id') == identifier:
            return p

    # Try folder name match (most intuitive for users)
    identifier_lower = identifier.lower()
    for p in projects:
        if p.get('folder_name', '').lower() == identifier_lower:
            return p

    # Try project name match
    for p in projects:
        if p.get('name', '').lower() == identifier_lower:
            return p

    # Try partial folder name match
    for p in projects:
        folder = p.get('folder_name', '').lower()
        if folder and identifier_lower in folder:
            return p

    return None


def _init_filesystem_at_path(target_path, project_id, name, description, project_type, tags, logger):
    """Initialize .empirica/ filesystem config at a given git repo path.

    Called by project-create --path to bridge workspace DB entry with filesystem config.
    Creates config.yaml and project.yaml without duplicating the DB creation that
    project-create already did.
    """
    import subprocess
    from datetime import datetime

    import yaml

    empirica_dir = target_path / '.empirica'
    empirica_dir.mkdir(exist_ok=True)
    sessions_dir = empirica_dir / 'sessions'
    sessions_dir.mkdir(exist_ok=True)

    # Create config.yaml if missing
    config_path = empirica_dir / 'config.yaml'
    if not config_path.exists():
        # Temporarily change cwd to target so create_default_config writes there
        import os

        from empirica.config.path_resolver import create_default_config
        old_cwd = os.getcwd()
        try:
            os.chdir(str(target_path))
            create_default_config()
        finally:
            os.chdir(old_cwd)

    # Create project.yaml
    project_yaml_path = empirica_dir / 'project.yaml'

    # Auto-detect git remote
    try:
        result = subprocess.run(
            ['git', '-C', str(target_path), 'remote', 'get-url', 'origin'],
            capture_output=True, text=True, timeout=5
        )
        git_url = result.stdout.strip() if result.returncode == 0 else None
    except Exception:
        git_url = None

    # Auto-detect languages
    languages = []
    for marker, lang in [('pyproject.toml', 'python'), ('package.json', 'javascript'),
                         ('go.mod', 'go'), ('Cargo.toml', 'rust')]:
        if (target_path / marker).exists():
            languages.append(lang)

    import os
    project_config = {
        'version': '2.0',
        'name': name,
        'description': description or f"{name} project",
        'project_id': project_id,
        'type': project_type or 'software',
        'domain': '',
        'classification': 'internal',
        'status': 'active',
        'evidence_profile': 'auto',
        'languages': languages,
        'tags': tags or [],
        'created_at': datetime.now().strftime('%Y-%m-%d'),
        'created_by': os.environ.get('USER', 'unknown'),
    }
    if git_url:
        project_config['repository'] = git_url
    project_config.update({
        'contacts': [],
        'engagements': [],
        'edges': [],
        'beads': {'default_enabled': False},
        'subjects': {},
        'auto_detect': {'enabled': True, 'method': 'path_match'},
        'domain_config': {},
    })

    with open(project_yaml_path, 'w') as f:
        yaml.dump(project_config, f, default_flow_style=False, sort_keys=False)

    logger.debug(f"Initialized filesystem at {empirica_dir}")
    return {
        "config_yaml": str(config_path),
        "project_yaml": str(project_yaml_path),
        "languages_detected": languages,
        "git_url": git_url,
    }


def handle_project_create_command(args):
    """Handle project-create command"""
    try:
        from empirica.data.repositories.projects import ProjectRepository
        from empirica.data.session_database import SessionDatabase

        # Parse arguments
        name = args.name
        description = getattr(args, 'description', None)
        repos_str = getattr(args, 'repos', None)
        project_type = getattr(args, 'type', None)
        tags_str = getattr(args, 'tags', None)
        parent = getattr(args, 'parent', None)

        # Parse repos JSON if provided
        repos = None
        if repos_str:
            repos = json.loads(repos_str)

        # Parse tags (comma-separated or JSON array)
        tags = None
        if tags_str:
            if tags_str.startswith('['):
                tags = json.loads(tags_str)
            else:
                tags = [t.strip() for t in tags_str.split(',')]

        # Validate project_type
        if project_type and project_type not in ProjectRepository.PROJECT_TYPES:
            print(f"⚠️  Unknown type '{project_type}'. Valid types: {', '.join(ProjectRepository.PROJECT_TYPES)}")
            project_type = 'software'

        # Create project
        db = SessionDatabase()
        project_id = db.create_project(
            name=name,
            description=description,
            repos=repos,
            project_type=project_type,
            project_tags=tags,
            parent_project_id=parent
        )
        db.close()

        # Register in workspace.db for cross-project visibility (project-list, project-switch)
        try:
            from empirica.config.path_resolver import get_git_root

            from .workspace_init import _register_in_workspace_db

            # Determine trajectory_path: use git root if in repo, otherwise first repo path
            git_root = get_git_root()
            if git_root:
                trajectory_path = str(git_root)
            elif repos and len(repos) > 0:
                trajectory_path = repos[0]
            else:
                trajectory_path = None

            if trajectory_path:
                _register_in_workspace_db(
                    project_id=project_id,
                    name=name,
                    trajectory_path=trajectory_path,
                    description=description,
                    git_remote_url=None  # Could extract from git if needed
                )
                logger.debug(f"Registered project {name} in workspace.db")
        except Exception as e:
            logger.warning(f"Failed to register in workspace.db: {e}")
            # Non-fatal - project still created in local DB

        # Bridge: if --path provided, also initialize filesystem config at that path
        init_result = None
        target_path = getattr(args, 'path', None)
        if target_path:
            from pathlib import Path
            target = Path(target_path).resolve()
            if not target.exists():
                logger.warning(f"Path does not exist: {target}")
            elif not (target / '.git').exists():
                logger.warning(f"Not a git repository: {target} (run 'git init' first)")
            else:
                init_result = _init_filesystem_at_path(
                    target, project_id, name, description,
                    project_type, tags, logger
                )

        # Format output
        if hasattr(args, 'output') and args.output == 'json':
            result = {
                "ok": True,
                "project_id": project_id,
                "name": name,
                "project_type": project_type or 'product',
                "tags": tags or [],
                "repos": repos or [],
                "parent_project_id": parent,
                "message": "Project created successfully"
            }
            if init_result:
                result["filesystem_init"] = init_result
            print(json.dumps(result, indent=2))
        else:
            print("✅ Project created successfully")
            print(f"   Project ID: {project_id}")
            print(f"   Name: {name}")
            print(f"   Type: {project_type or 'product'}")
            if tags:
                print(f"   Tags: {', '.join(tags)}")
            if description:
                print(f"   Description: {description}")
            if repos:
                print(f"   Repos: {', '.join(repos)}")
            if parent:
                print(f"   Parent: {parent}")
            if init_result:
                print(f"   📁 Filesystem initialized at: {target_path}")

        # Return None to avoid exit code issues and duplicate output
        return None

    except Exception as e:
        handle_cli_error(e, "Project create", getattr(args, 'verbose', False))
        return None


def handle_project_handoff_command(args):
    """Handle project-handoff command"""
    try:
        from empirica.data.session_database import SessionDatabase

        # Parse arguments
        project_id = args.project_id
        project_summary = args.summary
        key_decisions_str = getattr(args, 'key_decisions', None)
        patterns_str = getattr(args, 'patterns', None)
        remaining_work_str = getattr(args, 'remaining_work', None)

        # Parse JSON arrays
        key_decisions = json.loads(key_decisions_str) if key_decisions_str else None
        patterns = json.loads(patterns_str) if patterns_str else None
        remaining_work = json.loads(remaining_work_str) if remaining_work_str else None

        # Create project handoff
        db = SessionDatabase()
        handoff_id = db.create_project_handoff(
            project_id=project_id,
            project_summary=project_summary,
            key_decisions=key_decisions,
            patterns_discovered=patterns,
            remaining_work=remaining_work
        )

        # Get aggregated learning deltas
        total_deltas = db.aggregate_project_learning_deltas(project_id)

        db.close()

        # Format output
        if hasattr(args, 'output') and args.output == 'json':
            result = {
                "ok": True,
                "handoff_id": handoff_id,
                "project_id": project_id,
                "total_learning_deltas": total_deltas,
                "message": "Project handoff created successfully"
            }
            print(json.dumps(result, indent=2))
        else:
            print("✅ Project handoff created successfully")
            print(f"   Handoff ID: {handoff_id}")
            print(f"   Project: {project_id[:8]}...")
            print("\n📊 Total Learning Deltas:")
            for vector, delta in total_deltas.items():
                if delta != 0:
                    sign = "+" if delta > 0 else ""
                    print(f"      {vector}: {sign}{delta:.2f}")

        print(json.dumps({"handoff_id": handoff_id, "total_deltas": total_deltas}, indent=2))
        return 0

    except Exception as e:
        handle_cli_error(e, "Project handoff", getattr(args, 'verbose', False))
        return 1


def handle_project_list_command(args):
    """Handle project-list command - lists all projects from workspace database"""
    try:
        # Query workspace database for global projects
        projects = get_workspace_projects()

        # Format output
        if hasattr(args, 'output') and args.output == 'json':
            result = {
                "ok": True,
                "projects_count": len(projects),
                "projects": [
                    {
                        "id": p.get('id'),
                        "name": p.get('name'),
                        "folder_name": p.get('folder_name'),
                        "description": p.get('description'),
                        "status": p.get('status'),
                        "project_type": p.get('project_type'),
                        "total_findings": p.get('total_findings', 0),
                        "total_unknowns": p.get('total_unknowns', 0),
                        "total_goals": p.get('total_goals', 0),
                        "last_activity": p.get('updated_timestamp'),
                        "trajectory_path": p.get('trajectory_path')
                    }
                    for p in projects
                ]
            }
            print(json.dumps(result, indent=2))
        else:
            if not projects:
                print("📁 No projects found in workspace.")
                print("\nTip: Run 'empirica workspace-init' to scan and register projects.")
                return None

            print(f"📁 Found {len(projects)} project(s) in workspace:\n")
            for i, p in enumerate(projects, 1):
                name = p.get('name', 'Unknown')
                folder = p.get('folder_name', '')
                status = p.get('status', 'active')
                findings = p.get('total_findings', 0)
                unknowns = p.get('total_unknowns', 0)

                # Show folder name prominently (this is how users will switch)
                print(f"{i}. {folder} ({status})")
                if name != folder:
                    print(f"   Name: {name}")
                if p.get('description'):
                    desc = p['description'][:60] + '...' if len(p.get('description', '')) > 60 else p.get('description', '')
                    print(f"   {desc}")
                print(f"   📝 {findings} findings  ❓ {unknowns} unknowns")
                print()

            print("💡 Switch projects with: empirica project-switch <folder-name>")

        return None

    except Exception as e:
        handle_cli_error(e, "Project list", getattr(args, 'verbose', False))
        return None



def _handle_transaction_on_switch(dest_project_path, output_format) -> dict:
    """Handle open transactions when switching projects. Returns result dict."""
    try:
        from empirica.config.path_resolver import get_empirica_root
        from empirica.core.statusline_cache import get_instance_id

        current_empirica_root = None
        instance_id = get_instance_id()

        if instance_id:
            instance_file = Path.home() / '.empirica' / 'instance_projects' / f'{instance_id}.json'
            if instance_file.exists():
                try:
                    import json as _json
                    with open(instance_file) as f:
                        inst_project_path = _json.load(f).get('project_path')
                    if inst_project_path:
                        current_empirica_root = Path(inst_project_path) / '.empirica'
                except Exception:
                    pass

        if not current_empirica_root:
            current_empirica_root = get_empirica_root()
        if not current_empirica_root:
            return {"ok": True, "reason": "no_current_project"}

        suffix = R.instance_suffix()
        tx_path = current_empirica_root / f'active_transaction{suffix}.json'
        if not tx_path.exists():
            return {"ok": True, "reason": "no_transaction"}

        import json as _json
        with open(tx_path) as f:
            tx_data = _json.load(f)

        if tx_data.get('status') != 'open':
            return {"ok": True, "reason": "transaction_closed"}

        # Compare project paths (normalized)
        tx_proj = str(Path(tx_data.get('project_path', '')).resolve()) if tx_data.get('project_path') else ''
        dest_proj = str(Path(str(dest_project_path)).resolve()) if dest_project_path else ''

        if tx_proj == dest_proj:
            return {"ok": True, "reason": "transaction_preserved", "note": "Transaction is for destination project"}

        # Different project — abandon (no fake vectors)
        tx_id = tx_data.get('transaction_id', 'unknown')
        try:
            tx_path.unlink()
        except Exception:
            pass
        if output_format == 'human':
            print("⚠️  Previous transaction abandoned (submit POSTFLIGHT before switching to preserve deltas)")
        return {"ok": True, "reason": "transaction_abandoned", "transaction_id": tx_id}
    except Exception as e:
        logger.debug(f"Transaction handling on switch failed (non-fatal): {e}")
        return {"ok": False, "error": str(e)}


def handle_project_switch_command(args):
    """
    Handle project-switch command - Switch to a different project with context loading

    Resolves projects from workspace database by:
    1. Folder name (most intuitive - e.g., 'empirica-platform')
    2. Project name
    3. UUID

    Provides clear UX for AI agents:
    1. Resolves project from workspace database
    2. Shows "you are here" banner with project path
    3. Automatically runs project-bootstrap
    4. Shows next steps

    Does NOT create a session (explicit action for user)
    """
    try:
        project_identifier = args.project_identifier
        output_format = getattr(args, 'output', 'human')
        # Get claude_session_id from CLI arg (enables instance isolation via Bash tool)
        cli_claude_session_id = getattr(args, 'claude_session_id', None)

        # 1. Resolve project from workspace database
        project = resolve_workspace_project(project_identifier)

        if not project:
            error_msg = f"Project not found: {project_identifier}"
            hint = "Run 'empirica project-list' to see available projects"

            if output_format == 'json':
                print(json.dumps({
                    'ok': False,
                    'error': error_msg,
                    'hint': hint
                }))
            else:
                print(f"❌ {error_msg}")
                print(f"\nTip: {hint}")

            return None

        # 2. Extract project details
        project_id = project.get('id')
        project_name = project.get('name')
        folder_name = project.get('folder_name')
        trajectory_path = project.get('trajectory_path', '')

        # Derive project root from trajectory path
        # Two formats exist:
        # 1. Old format: /home/user/project/.empirica -> project_path = /home/user/project
        # 2. New format: /home/user/project (no .empirica suffix) -> use as-is
        project_path = None
        if trajectory_path:
            traj = Path(trajectory_path)
            if traj.name == '.empirica':
                # Old format: path ends with .empirica, take parent
                project_path = traj.parent
            else:
                # New format: path is the project root directly
                project_path = traj

        # 3. Handle open transaction from current project
        postflight_result = _handle_transaction_on_switch(project_path, output_format)

        # 4. SESSION CONTINUITY: Update global session registry with new project
        # Sessions are per-conversation (global), not per-project.
        # Project-switch just updates which project the session is working on.
        attached_session = None
        try:
            import sqlite3

            from empirica.core.statusline_cache import get_instance_id
            from empirica.data.repositories.sessions import update_session_project

            current_instance_id = get_instance_id()

            # Get current session from global registry
            workspace_db = Path.home() / '.empirica' / 'workspace' / 'workspace.db'
            if workspace_db.exists():
                conn = sqlite3.connect(str(workspace_db))
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()

                # Find active session for this instance
                if current_instance_id:
                    cursor.execute("""
                        SELECT session_id, ai_id, created_at
                        FROM global_sessions
                        WHERE instance_id = ? AND status = 'active'
                        ORDER BY last_activity DESC NULLS LAST, created_at DESC
                        LIMIT 1
                    """, (current_instance_id,))
                else:
                    # Fallback: most recent active session
                    cursor.execute("""
                        SELECT session_id, ai_id, created_at
                        FROM global_sessions
                        WHERE status = 'active'
                        ORDER BY last_activity DESC NULLS LAST, created_at DESC
                        LIMIT 1
                    """)

                row = cursor.fetchone()
                if row:
                    attached_session = {
                        'session_id': row['session_id'],
                        'ai_id': row['ai_id'],
                        'start_time': row['created_at']
                    }
                    # Update the session's current project in global registry
                    update_session_project(row['session_id'], project_id)
                conn.close()

            # 4b. ENSURE SESSION EXISTS IN TARGET PROJECT'S DB
            # The session from global_sessions may not exist in the target project's
            # per-project sessions.db (it was created in a different project).
            # The statusline reads from per-project DB, so a missing session = "inactive".
            if attached_session and project_path:
                try:
                    target_db_path = Path(project_path) / '.empirica' / 'sessions' / 'sessions.db'
                    if target_db_path.exists():
                        target_conn = sqlite3.connect(str(target_db_path))
                        target_cursor = target_conn.cursor()

                        # Check if session already exists in target DB
                        target_cursor.execute(
                            "SELECT session_id FROM sessions WHERE session_id = ?",
                            (attached_session['session_id'],)
                        )
                        if not target_cursor.fetchone():
                            # Session doesn't exist in target — mirror it
                            from datetime import datetime, timezone
                            target_cursor.execute("""
                                INSERT INTO sessions (
                                    session_id, ai_id, start_time, bootstrap_level,
                                    components_loaded, project_id, instance_id
                                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                            """, (
                                attached_session['session_id'],
                                attached_session['ai_id'],
                                attached_session.get('start_time') or datetime.now(timezone.utc).isoformat(),
                                1,  # bootstrap_level
                                0,  # components_loaded
                                project_id,
                                current_instance_id
                            ))
                            target_conn.commit()
                            if output_format == 'human':
                                print("📎 Session mirrored to target project database")
                        else:
                            # Session exists but may have wrong project_id — update it
                            target_cursor.execute(
                                "UPDATE sessions SET project_id = ? WHERE session_id = ?",
                                (project_id, attached_session['session_id'])
                            )
                            target_conn.commit()
                        target_conn.close()
                except Exception as e2:
                    logger.debug(f"Session mirroring to target DB failed (non-fatal): {e2}")
        except Exception as e:
            logger.debug(f"Session continuity update failed (non-fatal): {e}")

        # 4c. ENSURE PROJECT EXISTS IN TARGET'S LOCAL projects TABLE
        # Domain projects created via redistribution/workspace.db may have an empty
        # local projects table, causing finding-log, project-bootstrap, and other
        # commands to fail with "Project not found". Fix: auto-populate from workspace.db.
        if project_path and project_id:
            try:
                target_db_path = Path(project_path) / '.empirica' / 'sessions' / 'sessions.db'
                if target_db_path.exists():
                    target_conn = sqlite3.connect(str(target_db_path))
                    target_cursor = target_conn.cursor()

                    # Check if project already exists in target's local projects table
                    target_cursor.execute(
                        "SELECT id FROM projects WHERE id = ?",
                        (project_id,)
                    )
                    if not target_cursor.fetchone():
                        # Project missing from local DB — populate from workspace.db metadata
                        import time
                        now = time.time()
                        project_description = project.get('description', '')
                        proj_type = project.get('project_type', 'software')
                        proj_tags = project.get('project_tags', '')
                        created_ts = project.get('created_timestamp', now)

                        target_cursor.execute("""
                            INSERT INTO projects (
                                id, name, description, repos, created_timestamp,
                                last_activity_timestamp, status, metadata,
                                total_sessions, total_goals, total_epistemic_deltas,
                                project_data, project_type, project_tags, parent_project_id
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            project_id,
                            project_name or folder_name,
                            project_description,
                            None,  # repos
                            created_ts,
                            now,  # last_activity_timestamp
                            'active',
                            None,  # metadata
                            0,  # total_sessions
                            0,  # total_goals
                            None,  # total_epistemic_deltas
                            '{}',  # project_data (required NOT NULL)
                            proj_type,
                            proj_tags,
                            None  # parent_project_id
                        ))
                        target_conn.commit()
                        if output_format == 'human':
                            print("📦 Project record created in local database")
                    target_conn.close()
            except Exception as e3:
                logger.debug(f"Local projects table population failed (non-fatal): {e3}")

        # 5. Update active_work.json for cross-project continuity
        # This ensures pre-compact hook preserves project context even when Claude Code resets CWD
        # Include empirica_session_id so Sentinel and MCP tools can attach to the correct session
        if project_path:
            attached_session_id = attached_session['session_id'] if attached_session else None

            # AUTO-HEAL (same pattern as post-compact 11.24): if the global_sessions
            # mirror failed (instance_id mismatch, status filter, etc.) we end up with
            # attached_session_id=None, but the existing active_work file may still
            # know about a session_id from a prior conversation. Recover it and
            # ensure it exists in the target project's local DB so Sentinel,
            # statusline, and CLI lookups don't fail with "session NOT FOUND".
            if not attached_session_id and cli_claude_session_id:
                try:
                    aw_file = (
                        Path.home() / '.empirica' /
                        f'active_work_{cli_claude_session_id}.json'
                    )
                    if aw_file.exists():
                        with open(aw_file) as f:
                            existing_aw = json.load(f)
                        attached_session_id = existing_aw.get('empirica_session_id')
                        if attached_session_id and output_format == 'human':
                            print(
                                "🔄 Recovered session "
                                f"{attached_session_id[:8]} from active_work file"
                            )
                except Exception as e:
                    logger.debug(
                        f"Active_work session_id recovery failed (non-fatal): {e}"
                    )

            # If we have a session_id, make sure it exists in the target project's
            # local sessions.db. ensure_session_exists is idempotent: no-op if the
            # row already exists, inserts a minimal heal-row if missing.
            if attached_session_id and project_path:
                try:
                    target_db_path = (
                        Path(project_path) / '.empirica' / 'sessions' / 'sessions.db'
                    )
                    if target_db_path.exists():
                        from empirica.data.session_database import SessionDatabase
                        target_db = SessionDatabase(db_path=target_db_path)
                        healed = target_db.ensure_session_exists(
                            session_id=attached_session_id,
                            ai_id='claude-code',
                            project_id=project_id,
                        )
                        target_db.close()
                        if healed and output_format == 'human':
                            print(
                                "🩹 Auto-healed session "
                                f"{attached_session_id[:8]} into target project DB"
                            )
                except Exception as e:
                    logger.debug(
                        f"project-switch auto-heal failed (non-fatal): {e}"
                    )

            _update_active_work(str(project_path), folder_name, empirica_session_id=attached_session_id, claude_session_id=cli_claude_session_id)

        # 5b. Memory swap: switch the auto-memory directory to track the new
        # active project. When the harness CWD doesn't match project_path
        # (e.g. user is cd'd in repo A but switched the empirica project to
        # repo B), Claude Code's auto-memory loader still reads A's memory.
        # Swap B's memory contents into A's slot. (KNOWN_ISSUES 11.28)
        try:
            from empirica.utils.memory_swap import swap_memory
            cwd = Path.cwd().resolve()
            swap_result = swap_memory(
                harness_cwd_project=cwd,
                active_tx_project=Path(project_path),
                claude_session_id=cli_claude_session_id,
            )
            if swap_result.get("action") == "swapped" and output_format == 'human':
                print(f"💾 {swap_result.get('message', '')}")
        except Exception as e:
            logger.debug(f"project-switch memory swap failed (non-fatal): {e}")

        # 6. Query LIVE counts from per-project sessions.db (before output format branch)
        _sw_findings, _sw_unknowns, _sw_goals = 0, 0, 0
        if project_path:
            try:
                import sqlite3 as _sw_sql_pre
                _sw_db_pre = Path(project_path) / '.empirica' / 'sessions' / 'sessions.db'
                if _sw_db_pre.exists():
                    _sw_conn_pre = _sw_sql_pre.connect(str(_sw_db_pre))
                    _sw_c_pre = _sw_conn_pre.cursor()
                    try:
                        _sw_findings = _sw_c_pre.execute("SELECT COUNT(*) FROM project_findings WHERE project_id = ?", (project_id,)).fetchone()[0]
                    except Exception:
                        pass
                    try:
                        _sw_unknowns = _sw_c_pre.execute("SELECT COUNT(*) FROM project_unknowns WHERE project_id = ? AND is_resolved = 0", (project_id,)).fetchone()[0]
                    except Exception:
                        pass
                    try:
                        _sw_goals = _sw_c_pre.execute("SELECT COUNT(*) FROM goals WHERE project_id = ? AND is_completed = 0", (project_id,)).fetchone()[0]
                    except Exception:
                        pass
                    _sw_conn_pre.close()
            except Exception:
                pass

        # Show context banner
        if output_format == 'human':
            print()
            print("━" * 70)
            print("🎯 PROJECT CONTEXT SWITCH")
            print("━" * 70)
            print()
            print(f"📁 Project: {folder_name}")
            if project_name != folder_name:
                print(f"   Name: {project_name}")
            print(f"🆔 Project ID: {project_id[:8]}...")
            if project_path:
                print(f"📍 Location: {project_path}")
            print(f"📊 Findings: {_sw_findings}  Unknowns: {_sw_unknowns}  Goals: {_sw_goals}")
            if attached_session:
                print(f"🔗 Attached to session: {attached_session['session_id'][:8]}... (AI: {attached_session['ai_id']})")
            print()

            # Epistemic Brief — quantified project profile
            try:
                from empirica.core.epistemic_brief import generate_epistemic_brief, format_brief_human
                _sw_db_path_str = str(Path(project_path) / '.empirica' / 'sessions' / 'sessions.db') if project_path else None
                if _sw_db_path_str and Path(_sw_db_path_str).exists():
                    brief = generate_epistemic_brief(project_id, db_path=_sw_db_path_str)
                    if brief.get('knowledge_state', {}).get('total_artifacts', 0) > 0:
                        print(format_brief_human(brief))
            except Exception as _brief_err:
                logger.debug(f"Epistemic brief generation failed (non-fatal): {_brief_err}")

        # 7. AUTO-BOOTSTRAP: Load context for the new project
        bootstrap_result = None
        try:
            # Run project-bootstrap for the new project
            # Use --output json to capture result, but don't print it in human mode
            # If we found an attached session, pass it to bootstrap
            bootstrap_cmd = ['empirica', 'project-bootstrap', '--output', 'json']
            if attached_session:
                bootstrap_cmd.extend(['--session-id', attached_session['session_id']])
            if project_path:
                # Run in project directory to ensure correct context
                result = run_empirica_subprocess(
                    bootstrap_cmd,
                    timeout=30,
                    cwd=str(project_path)
                )
                if result.returncode == 0:
                    try:
                        import json as _json
                        bootstrap_result = _json.loads(result.stdout)
                        if output_format == 'human':
                            print("✅ Project context loaded (auto-bootstrap)")
                    except Exception:
                        bootstrap_result = {"ok": True, "note": "bootstrap ran but non-JSON output"}
                else:
                    bootstrap_result = {"ok": False, "error": result.stderr[:200]}
        except Exception as e:
            # Non-fatal - continue even if bootstrap fails
            bootstrap_result = {"ok": False, "error": str(e)}
            logger.debug(f"Auto-bootstrap on project-switch failed (non-fatal): {e}")

        # 8. NO AUTO-PREFLIGHT: The AI must submit its own PREFLIGHT with genuine
        # self-assessed vectors. System-generated vectors poison calibration data.
        # The Sentinel will nudge the AI to submit PREFLIGHT when it detects
        # no open transaction after project-switch.
        preflight_result = {
            "needed": True,
            "note": "Submit PREFLIGHT with your own vector self-assessment to open a transaction."
        }
        if output_format == 'human':
            print("📋 Submit PREFLIGHT to open a transaction (self-assess your vectors)")

        # 9. Show project context summary from LIVE per-project DB
        if output_format == 'human':
            if _sw_findings or _sw_unknowns or _sw_goals:
                print("📋 Project Context Summary:")
                print()
                if _sw_findings:
                    print(f"   📝 {_sw_findings} findings logged")
                if _sw_unknowns:
                    print(f"   ❓ {_sw_unknowns} unknowns tracked")
                if _sw_goals:
                    print(f"   🎯 {_sw_goals} goals defined")
                print()
            else:
                print("📋 No epistemic artifacts yet in this project.")
                print()

            # Suggest running bootstrap in project directory for full context
            if project_path and project_path.exists():
                print("💡 For full context, run in project directory:")
                print(f"   cd {project_path} && empirica project-bootstrap")
                print()

        # 10. Show next steps
        if output_format == 'human':
            print()
            print("━" * 70)
            print("💡 Next Steps")
            print("━" * 70)
            print()
            if preflight_result and preflight_result.get('ok'):
                print("  Transaction is open — you're in noetic phase.")
                print()
                print("  1. Investigate — log findings, unknowns, dead-ends")
                print()
                print("  2. CHECK when ready to act, POSTFLIGHT when work is complete")
            else:
                print("  1. Start a transaction (PREFLIGHT) to begin measured work")
                print()
                print("  2. Investigate before acting — log findings, unknowns, dead-ends")
                print()
                print("  3. CHECK when ready to proceed, POSTFLIGHT when work is complete")
            print()
            print("⚠️  All commands now write to this project's database.")
            print("    Findings, sessions, goals → stored in this project context.")
            print()
        elif output_format == 'json':
            result = {
                'ok': True,
                'project_id': project_id,
                'project_name': project_name,
                'folder_name': folder_name,
                'project_path': str(project_path) if project_path else None,
                'stats': {
                    'findings': _sw_findings,
                    'unknowns': _sw_unknowns,
                    'goals': _sw_goals
                },
                'next_steps': [
                    'Run PREFLIGHT to start a measured transaction',
                    'Investigate before acting — log findings and unknowns',
                    'CHECK when ready, POSTFLIGHT when complete'
                ],
                'postflight_result': postflight_result,
                'attached_session': attached_session,
                'bootstrap_result': bootstrap_result,
                'preflight_result': preflight_result
            }
            print(json.dumps(result, indent=2))

        return None

    except Exception as e:
        logger.exception(f"Error in project-switch: {e}")
        if output_format == 'json':
            print(json.dumps({'ok': False, 'error': str(e)}))
        else:
            print(f"❌ Error switching project: {e}")
        return None
