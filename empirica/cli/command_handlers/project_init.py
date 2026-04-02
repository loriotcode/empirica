#!/usr/bin/env python3
"""
Project Init Command - Initialize Empirica in a new git repository

Creates per-project configuration files:
- .empirica/config.yaml (database paths, settings)
- .empirica/project.yaml (project metadata, BEADS settings)
- docs/SEMANTIC_INDEX.yaml (optional, documentation index template)

Usage:
    cd my-new-project
    git init
    empirica project-init
    
Author: Rovo Dev
Date: 2025-12-19
"""

import json
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def _resolve_or_create_project(db, args, project_name, project_description,
                                project_config_path, git_url, project_type, tags, output_format):
    """Resolve existing project or create new one. Returns project_id."""
    import yaml
    project_id = None

    # Explicit --project-id flag
    explicit_id = getattr(args, 'project_id', None)
    if explicit_id:
        if output_format != 'json':
            print(f"   🔗 Linking to existing project: {explicit_id[:8]}...")
        return explicit_id

    # Check project.yaml for existing ID
    if project_config_path.exists():
        try:
            with open(project_config_path) as f:
                existing_id = (yaml.safe_load(f) or {}).get('project_id')
            if existing_id and db.get_project(existing_id):
                if output_format != 'json':
                    print(f"   ♻️  Reusing existing project_id: {existing_id[:8]}...")
                return existing_id
        except Exception:
            pass

    # Check by name
    existing = db.projects.get_project_by_name(project_name)
    if existing:
        if output_format != 'json':
            print(f"   ♻️  Found existing project by name: {existing['id'][:8]}...")
        return existing['id']

    # Create new
    return db.create_project(
        name=project_name, description=project_description,
        repos=[git_url] if git_url else None,
        project_type=project_type, project_tags=tags if tags else None)


def _register_project_in_workspace(project_id, project_name, project_description,
                                    git_root, git_url, project_config, output_format):
    """Register project in global workspace.db. Non-fatal on failure."""
    try:
        import json as _json_ws
        from .workspace_init import _register_in_workspace_db
        ws_metadata = _json_ws.dumps({
            k: project_config.get(k, d) for k, d in [
                ('domain', ''), ('classification', 'internal'), ('evidence_profile', 'auto'),
                ('languages', []), ('contacts', []), ('engagements', []), ('edges', []),
            ]
        })
        _register_in_workspace_db(
            project_id=project_id, name=project_name,
            trajectory_path=str(git_root / '.empirica'),
            description=project_description, git_remote_url=git_url,
            project_type=project_config.get('type', 'software'), metadata=ws_metadata)
        if output_format != 'json':
            print("   📋 Registered in workspace")
    except Exception as e:
        logger.warning(f"Failed to register in workspace.db: {e}")


def _collect_project_config_interactive(git_root) -> dict:
    """Collect project configuration via interactive prompts."""
    print("📋 Project Configuration\n")
    default_name = git_root.name
    project_name = input(f"Project name [{default_name}]: ").strip() or default_name
    project_description = input("Project description (optional): ").strip() or None

    type_choices = 'software|content|research|data|design|operations|strategic|engagement|legal'
    type_input = input(f"\nProject type [{type_choices}] (software): ").strip().lower()
    project_type = type_input if type_input in type_choices.split('|') else 'software'

    project_domain = input("Domain (e.g., ai/measurement, bio/genomics): ").strip()
    ep_input = input("Evidence profile [code/prose/hybrid/auto] (auto): ").strip().lower()
    evidence_profile = ep_input if ep_input in ('code', 'prose', 'hybrid', 'auto') else 'auto'

    enable_beads = input("\nEnable BEADS issue tracking by default? [y/N]: ").strip().lower() in ('y', 'yes')
    create_semantic_index = input("Create SEMANTIC_INDEX.yaml template? [y/N]: ").strip().lower() in ('y', 'yes')

    return {
        'project_name': project_name, 'project_description': project_description,
        'enable_beads': enable_beads, 'create_semantic_index': create_semantic_index,
        'project_type': project_type, 'project_domain': project_domain,
        'evidence_profile': evidence_profile, 'classification': 'internal',
        'languages': [], 'tags': [],
    }


def _collect_project_config_from_args(args, git_root) -> dict:
    """Collect project configuration from CLI args (non-interactive)."""
    return {
        'project_name': getattr(args, 'project_name', None) or git_root.name,
        'project_description': getattr(args, 'project_description', None),
        'enable_beads': getattr(args, 'enable_beads', False),
        'create_semantic_index': getattr(args, 'create_semantic_index', False),
        'project_type': getattr(args, 'type', None) or 'software',
        'project_domain': getattr(args, 'domain', None) or '',
        'evidence_profile': getattr(args, 'evidence_profile', None) or 'auto',
        'classification': getattr(args, 'classification', None) or 'internal',
        'languages': getattr(args, 'languages', None) or [],
        'tags': getattr(args, 'tags', None) or [],
    }


def handle_project_init_command(args):
    """Handle project-init command - initialize Empirica in a new repo"""
    try:
        from empirica.config.path_resolver import create_default_config, ensure_empirica_structure, get_git_root
        from empirica.data.session_database import SessionDatabase

        # Auto-detect non-interactive: explicit flag OR no TTY OR JSON output
        explicit_non_interactive = getattr(args, 'non_interactive', False)
        has_tty = sys.stdin.isatty() if hasattr(sys.stdin, 'isatty') else False
        output_format = getattr(args, 'output', 'default')
        interactive = not explicit_non_interactive and has_tty and output_format != 'json'

        # Check if in git repo — offer to init one if not
        git_root = get_git_root()
        if not git_root:
            import subprocess
            if interactive:
                response = input("Not in a git repository. Initialize one? [Y/n]: ").strip().lower()
                if response in ('n', 'no'):
                    print("Aborted. Run 'git init' manually, then try again.")
                    return None
                subprocess.run(['git', 'init'], check=True)
                git_root = get_git_root()
            elif output_format == 'json' or explicit_non_interactive:
                # Auto-init silently in non-interactive mode
                subprocess.run(['git', 'init'], capture_output=True, check=True)
                git_root = get_git_root()

            if not git_root:
                if output_format == 'json':
                    print(json.dumps({"ok": False, "error": "Not in a git repository"}))
                else:
                    print("Error: Not in a git repository. Run 'git init' first.")
                return None

        # Check if already initialized
        config_path = git_root / '.empirica' / 'config.yaml'
        if config_path.exists() and not getattr(args, 'force', False):
            if output_format == 'json':
                print(json.dumps({
                    "ok": False,
                    "error": "Empirica already initialized in this repo",
                    "hint": "Use --force to reinitialize"
                }, indent=2))
            else:
                print("❌ Empirica already initialized in this repo")
                print(f"   Config found: {config_path}")
                print("\nTip: Use --force to reinitialize")
            return None

        if output_format != 'json':
            print("🚀 Initializing Empirica in this repository...")
            print(f"   Git root: {git_root}\n")

        # Create directory structure
        ensure_empirica_structure()

        # Create config.yaml
        create_default_config()

        # Collect project configuration (interactive or from args)
        if interactive and output_format != 'json':
            config_input = _collect_project_config_interactive(git_root)
        else:
            config_input = _collect_project_config_from_args(args, git_root)

        project_name = config_input['project_name']
        project_description = config_input['project_description']
        enable_beads = config_input['enable_beads']
        create_semantic_index = config_input['create_semantic_index']
        project_type = config_input['project_type']
        project_domain = config_input['project_domain']
        evidence_profile = config_input['evidence_profile']
        classification = config_input['classification']
        languages = config_input['languages']
        tags = config_input['tags']

        # Create project.yaml with BEADS config
        project_config_path = git_root / '.empirica' / 'project.yaml'

        # Get git remote URL for repos field
        import subprocess
        try:
            result = subprocess.run(
                ['git', 'remote', 'get-url', 'origin'],
                capture_output=True,
                text=True,
                timeout=5
            )
            git_url = result.stdout.strip() if result.returncode == 0 else None
        except Exception:
            git_url = None

        # Auto-detect languages from build files if not specified
        if not languages:
            languages = _auto_detect_languages(git_root)

        from datetime import datetime
        project_config = {
            'version': '2.0',
            'name': project_name,
            'description': project_description or f"{project_name} project",
            'project_id': None,  # Placeholder — filled after DB creation
            'type': project_type,
            'domain': project_domain,
            'classification': classification,
            'status': 'active',
            'evidence_profile': evidence_profile,
            'languages': languages,
            'tags': tags,
            'created_at': datetime.now().strftime('%Y-%m-%d'),
            'created_by': os.environ.get('USER', 'unknown'),
        }
        if git_url:
            project_config['repository'] = git_url
        project_config.update({
            'contacts': [],
            'engagements': [],
            'edges': [],
            'beads': {
                'default_enabled': enable_beads,
            },
            'subjects': {},
            'auto_detect': {
                'enabled': True,
                'method': 'path_match'
            },
            'domain_config': {},
            'calibration_weights': _seed_calibration_weights(project_type),
        })

        import yaml
        with open(project_config_path, 'w') as f:
            yaml.dump(project_config, f, default_flow_style=False, sort_keys=False)

        # Create or reuse project in database (idempotent)
        # For new projects, we must pass explicit path since get_session_db_path()
        # requires an existing db or context to resolve
        db_path = git_root / '.empirica' / 'sessions' / 'sessions.db'
        db = SessionDatabase(db_path=str(db_path))
        project_id = None
        reused_existing = False

        # Resolve or create project ID
        project_id = _resolve_or_create_project(
            db, args, project_name, project_description, project_config_path,
            git_url, project_type, tags, output_format)

        # Update project.yaml with project_id
        project_config['project_id'] = project_id
        with open(project_config_path, 'w') as f:
            yaml.dump(project_config, f, default_flow_style=False, sort_keys=False)

        db.close()

        # Register in global workspace
        _register_project_in_workspace(
            project_id, project_name, project_description, git_root, git_url, project_config, output_format)

        # NOTE: project-init does NOT update resolver context (instance_projects,
        # TTY sessions, active_work). Those files route the current terminal to a
        # project — overwriting them here corrupts context for any existing session
        # in this terminal. Use `empirica project-switch` to change active project.

        # Create SEMANTIC_INDEX.yaml template if requested
        semantic_index_path = None
        if create_semantic_index:
            docs_dir = git_root / 'docs'
            docs_dir.mkdir(exist_ok=True)

            semantic_index_path = docs_dir / 'SEMANTIC_INDEX.yaml'

            template = {
                'version': '2.0',
                'project': project_name,
                'index': {
                    'README.md': {
                        'tags': ['readme', 'getting-started'],
                        'concepts': ['Project overview'],
                        'questions': ['What is this project?'],
                        'use_cases': ['new_user_onboarding']
                    }
                },
                'total_docs_indexed': 1,
                'last_updated': '2025-12-19',
                'coverage': {
                    'core_concepts': 1,
                    'quickstart': 0,
                    'architecture': 0,
                    'api': 0
                }
            }

            with open(semantic_index_path, 'w') as f:
                yaml.dump(template, f, default_flow_style=False, sort_keys=False)

        # Format output
        if output_format == 'json':
            result = {
                "ok": True,
                "project_id": project_id,
                "project_name": project_name,
                "git_root": str(git_root),
                "reused_existing": reused_existing,
                "files_created": {
                    "config": str(config_path),
                    "project_config": str(project_config_path),
                    "semantic_index": str(semantic_index_path) if semantic_index_path else None
                },
                "beads_enabled": enable_beads,
                "message": "Empirica initialized successfully" + (" (reused existing project)" if reused_existing else "")
            }
            print(json.dumps(result, indent=2))
        else:
            print("\n✅ Empirica initialized successfully!\n")
            print("📁 Files created:")
            print(f"   • {config_path.relative_to(git_root)}")
            print(f"   • {project_config_path.relative_to(git_root)}")
            if semantic_index_path:
                print(f"   • {semantic_index_path.relative_to(git_root)}")

            print(f"\n🆔 Project ID: {project_id}")
            print(f"📦 Project Name: {project_name}")
            if enable_beads:
                print(f"🔗 BEADS: Enabled by default")

            print("\n📋 Next steps:")
            if enable_beads:
                print("   1. Initialize BEADS issue tracking:")
                print(f"      bd init")
                print("   2. Create your first session:")
                print(f"      empirica session-create --ai-id myai")
                print("   3. Create goals (BEADS will auto-link):")
                print(f"      empirica goals-create --objective '...' --success-criteria '...'")
            else:
                print("   1. Create your first session:")
                print(f"      empirica session-create --ai-id myai")
                print("   2. Start working with epistemic tracking:")
                print(f"      empirica preflight-submit <assessment.json>")

            if create_semantic_index:
                print(f"\n📖 Semantic index template created!")
                print(f"   Edit docs/SEMANTIC_INDEX.yaml to add your documentation metadata")

        # Return result dict for programmatic use (e.g., auto-init)
        return {
            "ok": True,
            "project_id": project_id,
            "project_name": project_name,
            "git_root": str(git_root),
        }

    except Exception as e:
        from ..cli_utils import handle_cli_error
        handle_cli_error(e, "Project init", getattr(args, 'verbose', False))
        return None


def _seed_calibration_weights(project_type: str) -> dict:
    """Generate default per-phase per-vector calibration weights for a project type.

    These are Tier 2 static defaults seeded at project-init. They provide
    sensible starting weights based on domain and phase until the dynamic
    EMA adaptation (empirica-extended) refines them from actual calibration gaps.

    Based on evidence from 478 grounded verifications showing:
    - Noetic phase: generally well-calibrated, execution vectors irrelevant
    - Praxic phase: systematically overestimates know/context/completion,
      underestimates uncertainty/impact
    """
    # Map project_type to calibration domain
    _TYPE_TO_DOMAIN = {
        'software': 'software', 'content': 'consulting',
        'research': 'research', 'data': 'research',
        'design': 'consulting', 'operations': 'operations',
        'strategic': 'consulting', 'engagement': 'consulting',
        'legal': 'operations',
    }
    domain = _TYPE_TO_DOMAIN.get(project_type, 'default')

    # Base noetic weights: investigation phase — execution vectors low
    noetic_base = {
        'know': 1.0, 'context': 0.8, 'signal': 0.9, 'uncertainty': 1.0,
        'do': 0.3, 'change': 0.2, 'state': 0.3, 'completion': 0.5,
        'impact': 0.3, 'clarity': 0.7, 'coherence': 0.6, 'density': 0.4,
    }

    # Base praxic weights: from calibration gap evidence
    praxic_base = {
        'know': 1.0, 'completion': 1.0, 'context': 1.0,
        'impact': 0.9, 'uncertainty': 0.8, 'change': 0.8,
        'do': 0.7, 'clarity': 0.7, 'coherence': 0.7,
        'signal': 0.4, 'density': 0.4, 'state': 0.6,
    }

    # Domain-specific adjustments
    if domain == 'software':
        praxic_base['change'] = 0.9  # code changes matter more
        praxic_base['state'] = 0.7   # environment awareness
    elif domain == 'consulting':
        praxic_base['clarity'] = 0.9  # communication clarity critical
        noetic_base['context'] = 1.0  # client context essential
    elif domain == 'research':
        noetic_base['signal'] = 1.0   # signal detection critical
        praxic_base['completion'] = 0.6  # open-ended work
    elif domain == 'operations':
        praxic_base['state'] = 0.9   # system state awareness
        praxic_base['change'] = 0.9  # change tracking critical

    return {'noetic': noetic_base, 'praxic': praxic_base}


def _auto_detect_languages(git_root: Path) -> list:
    """Auto-detect programming languages from build/config files."""
    detected = []
    indicators = {
        'python': ['pyproject.toml', 'setup.py', 'setup.cfg', 'requirements.txt', 'Pipfile'],
        'typescript': ['tsconfig.json'],
        'javascript': ['package.json'],
        'go': ['go.mod'],
        'rust': ['Cargo.toml'],
        'java': ['pom.xml', 'build.gradle', 'build.gradle.kts'],
        'ruby': ['Gemfile'],
        'php': ['composer.json'],
        'c#': ['*.csproj', '*.sln'],
        'swift': ['Package.swift'],
        'r': ['DESCRIPTION', '.Rproj'],
    }
    for lang, files in indicators.items():
        for pattern in files:
            if '*' in pattern:
                if list(git_root.glob(pattern)):
                    detected.append(lang)
                    break
            elif (git_root / pattern).exists():
                detected.append(lang)
                break
    # Don't duplicate: if typescript detected, don't also add javascript from package.json
    if 'typescript' in detected and 'javascript' in detected:
        detected.remove('javascript')
    return detected
