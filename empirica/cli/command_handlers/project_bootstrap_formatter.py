"""
Bootstrap Output Formatter — extracted from project_bootstrap.py

Reduces handler complexity by separating output formatting (~600 lines)
from context loading logic (~120 lines).
"""

import json
import logging
import subprocess

from ..cli_utils import safe_print

logger = logging.getLogger(__name__)


def format_bootstrap_output(
    args, project_id, breadcrumbs, db=None,
    mco_config=None, workflow_suggestions=None,
    global_learnings=None, project_skills=None,
):
    """Format and print bootstrap output (JSON or human-readable)."""
    if hasattr(args, 'output') and args.output == 'json':
        result = {
            "ok": True,
            "project_id": project_id,
            "project_name": breadcrumbs.get('project', {}).get('name'),
            "breadcrumbs": breadcrumbs
        }
        if workflow_suggestions:
            result['workflow_automation'] = workflow_suggestions
        if mco_config:
            result['mco_config'] = mco_config
        if global_learnings:
            result['global_learnings'] = global_learnings
        if project_skills:
            result['project_skills'] = project_skills
        safe_print(json.dumps(result, indent=2))
    else:
        # Print MCO config first if post-compact (SessionStart hook)
        if mco_config:
            safe_print("\n" + "=" * 70)
            safe_print("🔧 MCO Configuration Restored (SessionStart Hook)")
            safe_print("=" * 70)
            if mco_config['source'] == 'pre_summary_snapshot':
                safe_print(f"   Source: {mco_config['snapshot_path']}")
            else:
                safe_print("   Source: Fresh load from MCO files (snapshot had no MCO)")
            safe_print("=" * 70)
            safe_print(mco_config['formatted'])
            safe_print("\n" + "=" * 70)
            safe_print("💡 Your configuration has been restored from pre-compact snapshot.")
            safe_print("   Apply these bias corrections during CASCADE assessments.")
            safe_print("=" * 70 + "\n")

        project = breadcrumbs['project']
        last = breadcrumbs['last_activity']

        # ===== PROJECT CONTEXT BANNER =====
        safe_print("━" * 64)
        safe_print("🎯 PROJECT CONTEXT")
        safe_print("━" * 64)
        safe_print()
        safe_print(f"📁 Project: {project['name']}")
        safe_print(f"🆔 ID: {project_id}")

        # Get git URL
        git_url = None
        try:
            result = subprocess.run(
                ['git', 'remote', 'get-url', 'origin'],
                capture_output=True,
                text=True,
                timeout=2
            )
            if result.returncode == 0:
                git_url = result.stdout.strip()
                safe_print(f"🔗 Repository: {git_url}")
        except Exception:
            pass

        safe_print(f"📍 Location: {db.db_path.parent.parent if hasattr(db, 'db_path') and db.db_path else 'Unknown'}")
        safe_print("💾 Database: .empirica/sessions/sessions.db")
        safe_print()
        safe_print("⚠️  All commands write to THIS project's database.")
        safe_print("   Findings, sessions, goals → stored in this project context.")
        safe_print()
        safe_print("━" * 64)
        safe_print()

        # ===== PROJECT SUMMARY =====
        safe_print("📋 Project Summary")
        safe_print(f"   {project['description']}")
        if project['repos']:
            safe_print(f"   Repos: {', '.join(project['repos'])}")
        safe_print(f"   Total sessions: {project['total_sessions']}")
        safe_print()

        safe_print("🕐 Last Activity:")
        safe_print(f"   {last['summary']}")
        safe_print(f"   Next focus: {last['next_focus']}")
        safe_print()

        # ===== AI EPISTEMIC HANDOFF =====
        if breadcrumbs.get('ai_epistemic_handoff'):
            handoff = breadcrumbs['ai_epistemic_handoff']
            safe_print(f"🧠 Epistemic Handoff (from {handoff.get('ai_id', 'unknown')}):")
            vectors = handoff.get('vectors', {})
            deltas = handoff.get('deltas', {})

            if vectors:
                safe_print("   State (POSTFLIGHT):")
                safe_print(f"      Engagement: {vectors.get('engagement', 'N/A'):.2f}", end='')
                if 'engagement' in deltas and deltas['engagement'] is not None:
                    delta = deltas['engagement']
                    arrow = "↑" if delta > 0 else "↓" if delta < 0 else "→"
                    safe_print(f" {arrow} {delta:+.2f}", end='')
                safe_print()

                if 'foundation' in vectors:
                    f = vectors['foundation']
                    d = deltas.get('foundation', {})
                    safe_print(f"      Foundation: know={f.get('know', 'N/A'):.2f}", end='')
                    if d.get('know') is not None:
                        safe_print(f" {d['know']:+.2f}", end='')
                    safe_print(f", do={f.get('do', 'N/A'):.2f}", end='')
                    if d.get('do') is not None:
                        safe_print(f" {d['do']:+.2f}", end='')
                    safe_print(f", context={f.get('context', 'N/A'):.2f}", end='')
                    if d.get('context') is not None:
                        safe_print(f" {d['context']:+.2f}", end='')
                    safe_print()

                safe_print(f"      Uncertainty: {vectors.get('uncertainty', 'N/A'):.2f}", end='')
                if 'uncertainty' in deltas and deltas['uncertainty'] is not None:
                    delta = deltas['uncertainty']
                    arrow = "↓" if delta < 0 else "↑" if delta > 0 else "→"  # Lower is better
                    safe_print(f" {arrow} {delta:+.2f}", end='')
                safe_print()

            if handoff.get('reasoning'):
                safe_print(f"   Learning: {handoff['reasoning'][:80]}...")
            safe_print()

        # ===== FLOW STATE METRICS =====
        if breadcrumbs.get('flow_metrics'):
            flow = breadcrumbs['flow_metrics']
            current = flow.get('current_flow')

            if current:
                safe_print("⚡ Flow State (AI Productivity):")
                safe_print(f"   Current: {current['emoji']} {current['flow_state']} ({current['flow_score']}/100)")

                # Show trend if available
                trend = flow.get('trend', {})
                if trend.get('emoji'):
                    safe_print(f"   Trend: {trend['emoji']} {trend['description']}")

                # Show average
                avg = flow.get('average_flow', 0)
                safe_print(f"   Average (last 5): {avg}/100")

                # Show blockers if any
                blockers = flow.get('blockers', [])
                if blockers:
                    safe_print("   ⚠️  Blockers:")
                    for blocker in blockers[:3]:
                        safe_print(f"      • {blocker}")

                # Show flow triggers status
                triggers = flow.get('triggers_present', {})
                if triggers:
                    active_triggers = [name for name, present in triggers.items() if present]
                    if active_triggers:
                        safe_print(f"   ✓ Active triggers: {', '.join(active_triggers)}")

                safe_print()

        # ===== HEALTH SCORE (EPISTEMIC QUALITY) =====
        if breadcrumbs.get('health_score'):
            health = breadcrumbs['health_score']
            current = health.get('current_health')

            if current:
                safe_print("💪 Health Score (Epistemic Quality):")
                safe_print(f"   Current: {current['health_score']}/100")

                # Show trend if available
                trend = health.get('trend', {})
                if trend.get('emoji'):
                    safe_print(f"   Trend: {trend['emoji']} {trend['description']}")

                # Show average
                avg = health.get('average_health', 0)
                safe_print(f"   Average (last 5): {avg}/100")

                # Show component breakdown
                components = health.get('components', {})
                if components:
                    safe_print("   Components:")
                    kq = components.get('knowledge_quality', {})
                    ep = components.get('epistemic_progress', {})
                    cap = components.get('capability', {})
                    conf = components.get('confidence', {})
                    eng = components.get('engagement', {})

                    safe_print(f"      Knowledge Quality: {kq.get('average', 0):.2f}")
                    safe_print(f"      Epistemic Progress: {ep.get('average', 0):.2f}")
                    safe_print(f"      Capability: {cap.get('average', 0):.2f}")
                    safe_print(f"      Confidence: {conf.get('confidence_score', 0):.2f}")
                    safe_print(f"      Engagement: {eng.get('engagement', 0):.2f}")
                safe_print()

        if breadcrumbs.get('findings'):
            safe_print("📝 Recent Findings (last 10):")
            for i, f in enumerate(breadcrumbs['findings'][:10], 1):
                safe_print(f"   {i}. {f}")
            safe_print()

        if breadcrumbs.get('unknowns'):
            unresolved = [u for u in breadcrumbs['unknowns'] if not u['is_resolved']]
            if unresolved:
                safe_print("❓ Unresolved Unknowns:")
                for i, u in enumerate(unresolved[:5], 1):
                    safe_print(f"   {i}. {u['unknown']}")
                safe_print()

        if breadcrumbs.get('dead_ends'):
            safe_print("💀 Dead Ends (What Didn't Work):")
            for i, d in enumerate(breadcrumbs['dead_ends'][:5], 1):
                safe_print(f"   {i}. {d['approach']}")
                safe_print(f"      → Why: {d['why_failed']}")
            safe_print()

        if breadcrumbs['mistakes_to_avoid']:
            safe_print("⚠️  Recent Mistakes to Avoid:")
            for i, m in enumerate(breadcrumbs['mistakes_to_avoid'][:3], 1):
                cost = m.get('cost_estimate', 'unknown')
                cause = m.get('root_cause_vector', 'unknown')
                safe_print(f"   {i}. {m['mistake']} (cost: {cost}, cause: {cause})")
                safe_print(f"      → {m['prevention']}")
            safe_print()

        if breadcrumbs.get('key_decisions'):
            safe_print("💡 Key Decisions:")
            for i, d in enumerate(breadcrumbs['key_decisions'], 1):
                safe_print(f"   {i}. {d}")
            safe_print()

        if breadcrumbs.get('reference_docs'):
            safe_print("📄 Reference Docs:")
            for i, doc in enumerate(breadcrumbs['reference_docs'][:5], 1):
                path = doc.get('doc_path', 'unknown')
                doc_type = doc.get('doc_type', 'unknown')
                safe_print(f"   {i}. {path} ({doc_type})")
                if doc.get('description'):
                    safe_print(f"      {doc['description']}")
            safe_print()

        if breadcrumbs.get('recent_artifacts'):
            safe_print("📝 Recently Modified Files (last 10 sessions):")
            for i, artifact in enumerate(breadcrumbs['recent_artifacts'][:10], 1):
                safe_print(f"   {i}. Session {artifact['session_id']} ({artifact['ai_id']})")
                safe_print(f"      Task: {artifact['task_summary']}")
                safe_print(f"      Files modified ({len(artifact['files_modified'])}):")
                for file in artifact['files_modified'][:5]:  # Show first 5 files
                    safe_print(f"        • {file}")
                if len(artifact['files_modified']) > 5:
                    safe_print(f"        ... and {len(artifact['files_modified']) - 5} more")
            safe_print()

        # ===== NEW: Active Work Section =====
        if breadcrumbs.get('active_sessions') or breadcrumbs.get('active_goals'):
            safe_print("🚀 Active Work (In Progress):")
            safe_print()

            # Show active sessions
            if breadcrumbs.get('active_sessions'):
                safe_print("   📡 Active Sessions:")
                for sess in breadcrumbs['active_sessions'][:3]:
                    from datetime import datetime
                    start = datetime.fromisoformat(str(sess['start_time']))
                    elapsed = datetime.now() - start
                    hours = int(elapsed.total_seconds() / 3600)
                    safe_print(f"      • {sess['session_id'][:8]}... ({sess['ai_id']}) - {hours}h ago")
                    if sess.get('subject'):
                        safe_print(f"        Subject: {sess['subject']}")
                safe_print()

            # Show active goals
            if breadcrumbs.get('active_goals'):
                safe_print("   🎯 Goals In Progress:")
                for goal in breadcrumbs['active_goals'][:5]:
                    beads_link = f" [BEADS: {goal['beads_issue_id']}]" if goal.get('beads_issue_id') else " ⚠️ No BEADS link"
                    safe_print(f"      • [{goal['id'][:8]}] {goal['objective']}{beads_link}")
                    safe_print(f"        AI: {goal['ai_id']} | Subtasks: {goal['subtask_count']}")

                    # Show recent findings for this goal
                    goal_findings = [f for f in breadcrumbs.get('findings_with_goals', []) if f['goal_id'] == goal['id']]
                    if goal_findings:
                        safe_print(f"        Latest: {goal_findings[0]['finding'][:60]}...")
                safe_print()

            # Show epistemic artifacts
            if breadcrumbs.get('epistemic_artifacts'):
                safe_print("   📊 Epistemic Artifacts:")
                for artifact in breadcrumbs['epistemic_artifacts'][:3]:
                    size_kb = artifact['size'] / 1024
                    safe_print(f"      • {artifact['path']} ({size_kb:.1f} KB)")
                safe_print()

            # Show AI activity summary
            if breadcrumbs.get('ai_activity'):
                safe_print("   👥 AI Activity (Last 7 Days):")
                for ai in breadcrumbs['ai_activity'][:5]:
                    safe_print(f"      • {ai['ai_id']}: {ai['session_count']} session(s)")
                safe_print()
                safe_print("   💡 Tip: Use format '<model>-<workstream>' (e.g., claude-cli-testing)")
                safe_print()

        # ===== END NEW =====

        # ===== FLOW STATE METRICS =====
        if breadcrumbs.get('flow_metrics') is not None:
            safe_print("📊 Flow State Analysis (Recent Sessions):")
            safe_print()

            flow_metrics = breadcrumbs['flow_metrics']
            flow_data = flow_metrics.get('flow_scores', [])
            if flow_data:
                for i, session in enumerate(flow_data[:5], 1):
                    score = session['flow_score']
                    # Choose emoji based on score
                    if score >= 0.9:
                        emoji = "⭐"
                    elif score >= 0.7:
                        emoji = "🟢"
                    elif score >= 0.5:
                        emoji = "🟡"
                    else:
                        emoji = "🔴"

                    safe_print(f"   {i}. {session['session_id']} ({session['ai_id']})")
                    safe_print(f"      Flow Score: {score:.2f} {emoji}")

                    # Show top 3 components
                    components = session['components']
                    top_3 = sorted(components.items(), key=lambda x: x[1], reverse=True)[:3]
                    safe_print(f"      Top factors: {', '.join([f'{k}={v:.2f}' for k, v in top_3])}")

                    # Show recommendations if any
                    if session['recommendations']:
                        safe_print(f"      💡 {session['recommendations'][0]}")
                    safe_print()

                # Show what creates flow
                safe_print("   💡 Flow Triggers (Optimize for these):")
                safe_print("      ✅ CASCADE complete (PREFLIGHT → POSTFLIGHT)")
                safe_print("      ✅ Bootstrap loaded early")
                safe_print("      ✅ Goal with subtasks")
                safe_print("      ✅ CHECK for high-scope work")
                safe_print("      ✅ AI naming convention (<model>-<workstream>)")
                safe_print()
            else:
                safe_print("   💡 No completed sessions yet")
                safe_print("   Tip: Close active sessions with POSTFLIGHT to see flow metrics")
                safe_print("   Flow score will show patterns from completed work")
                safe_print()

        # ===== DATABASE SCHEMA SUMMARY =====
        if breadcrumbs.get('database_summary'):
            safe_print("🗄️  Database Schema (Epistemic Data Store):")
            safe_print()

            db_summary = breadcrumbs['database_summary']
            safe_print(f"   Total Tables: {db_summary.get('total_tables', 0)}")
            safe_print(f"   Tables With Data: {db_summary.get('tables_with_data', 0)}")
            safe_print()

            # Show key tables (static knowledge reminder)
            if db_summary.get('key_tables'):
                safe_print("   📌 Key Tables:")
                for table, description in list(db_summary['key_tables'].items())[:6]:
                    safe_print(f"      • {table}: {description}")
                safe_print()

            # Show top tables by row count
            if db_summary.get('top_tables'):
                safe_print("   📊 Most Active Tables:")
                for table_info in db_summary['top_tables'][:5]:
                    safe_print(f"      • {table_info}")
                safe_print()

            # Reference to full schema
            if db_summary.get('schema_doc'):
                safe_print(f"   📖 Full Schema: {db_summary['schema_doc']}")
                safe_print()

        # ===== STRUCTURE HEALTH =====
        if breadcrumbs.get('structure_health'):
            safe_print("🏗️  Project Structure Health:")
            safe_print()

            health = breadcrumbs['structure_health']

            # Show detected pattern with confidence
            # .get() returns None if key exists with None value — guard against it
            confidence = health.get('confidence') or 0.0
            conformance = health.get('conformance') or 0.0

            # Choose emoji based on conformance
            if conformance >= 0.9:
                emoji = "✅"
            elif conformance >= 0.7:
                emoji = "🟢"
            elif conformance >= 0.5:
                emoji = "🟡"
            else:
                emoji = "🔴"

            safe_print(f"   Detected Pattern: {health.get('detected_name', 'Unknown')} {emoji}")
            safe_print(f"   Detection Confidence: {confidence:.2f}")
            safe_print(f"   Pattern Conformance: {conformance:.2f}")
            safe_print(f"   Description: {health.get('description', '')}")
            safe_print()

            # Show violations if any
            violations = health.get('violations', [])
            if violations:
                safe_print(f"   ⚠️  Conformance Issues ({len(violations)}):")
                for violation in violations[:3]:
                    safe_print(f"      • {violation}")
                if len(violations) > 3:
                    safe_print(f"      ... and {len(violations) - 3} more")
                safe_print()

            # Show suggestions
            suggestions = health.get('suggestions', [])
            if suggestions:
                safe_print("   💡 Suggestions:")
                for suggestion in suggestions[:3]:
                    safe_print(f"      {suggestion}")
                safe_print()

        # ===== DEPENDENCY GRAPH =====
        if breadcrumbs.get('dependency_graph'):
            dep = breadcrumbs['dependency_graph']
            safe_print(f"📊 Project Dependencies ({dep.get('module_count', '?')} modules):")
            safe_print()
            if dep.get('hotspots'):
                safe_print("   🔥 Coupling Hotspots:")
                for h in dep['hotspots'][:5]:
                    safe_print(f"      {h['module']} ({h['importers']} importers)")
            if dep.get('entry_points'):
                safe_print(f"   🚀 Entry Points: {', '.join(dep['entry_points'][:5])}")
            if dep.get('external_deps'):
                safe_print(f"   📦 External: {', '.join(sorted(dep['external_deps'])[:10])}")
            safe_print()

        if breadcrumbs['incomplete_work']:
            safe_print("🎯 Incomplete Work:")
            for i, w in enumerate(breadcrumbs['incomplete_work'], 1):
                objective = w.get('objective', w.get('goal', 'Unknown'))
                status = w.get('status', 'unknown')
                safe_print(f"   {i}. {objective} ({status})")
            safe_print()

        if breadcrumbs.get('available_skills'):
            safe_print("🛠️  Available Skills:")
            for i, skill in enumerate(breadcrumbs['available_skills'], 1):
                tags = ', '.join(skill.get('tags', [])) if skill.get('tags') else 'no tags'
                safe_print(f"   {i}. {skill['title']} ({skill['id']})")
                safe_print(f"      Tags: {tags}")
            safe_print()

        if breadcrumbs.get('semantic_docs'):
            safe_print("📖 Core Documentation:")
            for i, doc in enumerate(breadcrumbs['semantic_docs'][:3], 1):
                safe_print(f"   {i}. {doc['title']}")
                safe_print(f"      Path: {doc['path']}")
            safe_print()

        if breadcrumbs.get('integrity_analysis'):
            safe_print("🔍 Doc-Code Integrity Analysis:")
            integrity = breadcrumbs['integrity_analysis']

            if 'error' in integrity:
                safe_print(f"   ⚠️  Analysis failed: {integrity['error']}")
            else:
                cli = integrity['cli_commands']
                safe_print(f"   Score: {cli['integrity_score']:.1%} ({cli['total_in_code']} code, {cli['total_in_docs']} docs)")

                if integrity.get('missing_code'):
                    safe_print(f"\n   🔴 Missing Implementations ({cli['missing_implementations']} total):")
                    for item in integrity['missing_code'][:5]:
                        safe_print(f"      • empirica {item['command']} (severity: {item['severity']})")
                        if item['mentioned_in']:
                            safe_print(f"        Mentioned in: {item['mentioned_in'][0]['file']}")

                if integrity.get('missing_docs'):
                    safe_print(f"\n   📝 Missing Documentation ({cli['missing_documentation']} total):")
                    for item in integrity['missing_docs'][:5]:
                        safe_print(f"      • empirica {item['command']}")
            safe_print()

        # Workflow Automation Suggestions (if session-id provided)
        if workflow_suggestions:
            from empirica.cli.utils.workflow_suggestions import format_workflow_suggestions
            workflow_output = format_workflow_suggestions(workflow_suggestions)
            if workflow_output.strip():
                safe_print(workflow_output)

        # Memory Gap Analysis (if session-id provided)
        if breadcrumbs.get('memory_gap_analysis'):
            analysis = breadcrumbs['memory_gap_analysis']
            enforcement = analysis.get('enforcement_mode', 'inform')

            # Select emoji based on enforcement mode
            mode_emoji = {
                'inform': '🧠',
                'warn': '⚠️',
                'strict': '🔴',
                'block': '🛑'
            }.get(enforcement, '🧠')

            safe_print(f"{mode_emoji} Memory Gap Analysis (Mode: {enforcement.upper()}):")

            if analysis['detected']:
                gap_score = analysis['overall_gap']
                claimed = analysis['claimed_know']
                expected = analysis['expected_know']

                safe_print("   Knowledge Assessment:")
                safe_print(f"      Claimed KNOW:  {claimed:.2f}")
                safe_print(f"      Expected KNOW: {expected:.2f}")
                safe_print(f"      Gap Score:     {gap_score:.2f}")

                # Group gaps by type
                gaps_by_type = {}
                for gap in breadcrumbs.get('memory_gaps', []):
                    gap_type = gap['type']
                    if gap_type not in gaps_by_type:
                        gaps_by_type[gap_type] = []
                    gaps_by_type[gap_type].append(gap)

                # Display gaps by severity
                if gaps_by_type:
                    safe_print("\n   Detected Gaps:")

                    # Priority order
                    type_order = ['confabulation', 'unreferenced_findings', 'unincorporated_unknowns',
                                 'file_unawareness', 'compaction']

                    for gap_type in type_order:
                        if gap_type not in gaps_by_type:
                            continue

                        gaps = gaps_by_type[gap_type]
                        severity_icon = {
                            'critical': '🔴',
                            'high': '🟠',
                            'medium': '🟡',
                            'low': '🔵'
                        }

                        # Show type header
                        type_label = gap_type.replace('_', ' ').title()
                        safe_print(f"\n      {type_label} ({len(gaps)}):")

                        # Show top 3 gaps of this type
                        for gap in gaps[:3]:
                            icon = severity_icon.get(gap['severity'], '•')
                            content = gap['content'][:80] + '...' if len(gap['content']) > 80 else gap['content']
                            safe_print(f"      {icon} {content}")
                            if gap.get('resolution_action'):
                                safe_print(f"         → {gap['resolution_action']}")

                        if len(gaps) > 3:
                            safe_print(f"         ... and {len(gaps) - 3} more")

                # Show recommended actions
                if analysis.get('recommended_actions'):
                    safe_print("\n   Recommended Actions:")
                    for i, action in enumerate(analysis['recommended_actions'][:5], 1):
                        safe_print(f"      {i}. {action}")
            else:
                safe_print("   ✅ No memory gaps detected - context is current")

            safe_print()

        # ===== WORKSPACE CONTEXT (plugin hook) =====
        # If empirica-workspace is installed, load project-type-aware context
        try:
            from empirica_workspace.bootstrap.project_context import (
                get_project_bootstrap_context,
                render_workspace_context,
            )
            # Look up project_type from workspace.db
            ws_project_type = 'product'
            try:
                import sqlite3 as _sqlite3
                from pathlib import Path as _Path
                ws_db_path = _Path.home() / ".empirica" / "workspace" / "workspace.db"
                if ws_db_path.exists():
                    ws_conn = _sqlite3.connect(str(ws_db_path))
                    ws_cur = ws_conn.cursor()
                    ws_cur.execute(
                        "SELECT project_type FROM global_projects WHERE id = ?",
                        (project_id,)
                    )
                    ws_row = ws_cur.fetchone()
                    if ws_row and ws_row[0]:
                        ws_project_type = ws_row[0]
                    ws_conn.close()
            except Exception:
                pass

            # Resolve project root from active context
            ws_project_root = None
            try:
                from empirica.utils.session_resolver import InstanceResolver as R
                ws_project_root = R.project_path()
            except Exception:
                pass
            if not ws_project_root:
                ws_project_root = os.getcwd()

            proj_info = breadcrumbs.get('project', {})
            workspace_ctx = get_project_bootstrap_context(
                project_id=proj_info.get('id', project_id),
                project_type=ws_project_type,
                project_root=ws_project_root,
            )
            if workspace_ctx:
                rendered = render_workspace_context(workspace_ctx)
                if rendered.strip():
                    safe_print(rendered)
        except ImportError:
            pass  # empirica-workspace not installed
        except Exception as ws_err:
            logger.debug("Workspace context hook failed: %s", ws_err)

