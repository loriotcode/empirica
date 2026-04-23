"""
Graph Artifact Commands — batch logging and resolution of connected artifacts.

Implements the Artifact Graph API (spec: empirica-cortex/.empirica/plans/artifact-graph-api.md).
Nodes are typed artifacts, edges are relationships between them.
"""

import json
import logging
import sys

from ..cli_utils import handle_cli_error

logger = logging.getLogger(__name__)

# Node types and their required fields
NODE_REQUIRED_FIELDS = {
    'finding': ['finding'],
    'unknown': ['unknown'],
    'dead_end': ['approach', 'why_failed'],
    'mistake': ['mistake', 'why_wrong'],
    'assumption': ['assumption', 'confidence'],
    'decision': ['choice', 'rationale'],
    'source': ['title'],
}

# Valid edge relation types
VALID_RELATIONS = {
    'evidence', 'raised_by', 'grounded_by', 'resolves',
    'invalidates', 'sourced_from', 'caused_by', 'prevents', 'attached_to',
}

# Creation order — dependencies resolved top-down
CREATION_ORDER = ['source', 'finding', 'unknown', 'dead_end', 'mistake', 'assumption', 'decision']


def _validate_graph(graph: dict) -> list[str]:
    """Validate graph structure. Returns list of errors (empty = valid)."""
    errors = []
    nodes = graph.get('nodes', [])
    edges = graph.get('edges', [])

    if not nodes:
        errors.append("No nodes provided")
        return errors

    refs = set()
    for i, node in enumerate(nodes):
        ref = node.get('ref')
        ntype = node.get('type')
        data = node.get('data', {})

        if not ref:
            errors.append(f"Node {i}: missing 'ref'")
            continue
        if ref in refs:
            errors.append(f"Node {i}: duplicate ref '{ref}'")
        refs.add(ref)

        if ntype not in NODE_REQUIRED_FIELDS:
            errors.append(f"Node '{ref}': unknown type '{ntype}' (valid: {', '.join(NODE_REQUIRED_FIELDS)})")
            continue

        for field in NODE_REQUIRED_FIELDS[ntype]:
            if field not in data:
                errors.append(f"Node '{ref}' ({ntype}): missing required field '{field}'")

    for i, edge in enumerate(edges):
        from_ref = edge.get('from')
        to_ref = edge.get('to')
        relation = edge.get('relation')

        if not from_ref or not to_ref:
            errors.append(f"Edge {i}: missing 'from' or 'to'")
            continue
        if relation not in VALID_RELATIONS:
            errors.append(f"Edge {i}: unknown relation '{relation}' (valid: {', '.join(sorted(VALID_RELATIONS))})")

        # Refs must exist in nodes (or be UUIDs for existing artifacts)
        if from_ref not in refs and not _is_uuid(from_ref):
            errors.append(f"Edge {i}: 'from' ref '{from_ref}' not found in nodes")
        if to_ref not in refs and not _is_uuid(to_ref):
            errors.append(f"Edge {i}: 'to' ref '{to_ref}' not found in nodes")

    return errors


def _is_uuid(s: str) -> bool:
    """Check if string looks like a UUID."""
    import re
    return bool(re.match(r'^[0-9a-f]{8}-[0-9a-f]{4}-', s, re.I))


def _create_node(db, node: dict, context: dict) -> str | None:
    """Create a single artifact node. Returns the UUID or None on failure."""
    ntype = node['type']
    data = node['data']
    session_id = context['session_id']
    project_id = context['project_id']
    goal_id = data.get('goal_id') or context.get('goal_id')
    transaction_id = context.get('transaction_id')

    try:
        if ntype == 'finding':
            return db.log_project_finding(
                project_id=project_id, session_id=session_id,
                finding=data['finding'], impact=data.get('impact', 0.5),
                goal_id=goal_id, subject=data.get('subject'),
                transaction_id=transaction_id,
            )
        elif ntype == 'unknown':
            return db.log_project_unknown(
                project_id=project_id, session_id=session_id,
                unknown=data['unknown'],
                goal_id=goal_id, subject=data.get('subject'),
                transaction_id=transaction_id,
            )
        elif ntype == 'dead_end':
            return db.log_project_dead_end(
                project_id=project_id, session_id=session_id,
                approach=data['approach'], why_failed=data['why_failed'],
                impact=data.get('impact', 0.5),
                goal_id=goal_id, subject=data.get('subject'),
                transaction_id=transaction_id,
            )
        elif ntype == 'mistake':
            return db.log_session_mistake(
                session_id=session_id,
                mistake=data['mistake'], why_wrong=data['why_wrong'],
                cost_estimate=data.get('cost_estimate'),
                root_cause_vector=data.get('root_cause_vector'),
                prevention=data.get('prevention'),
                goal_id=goal_id,
            )
        elif ntype == 'assumption':
            return db.log_project_assumption(
                project_id=project_id, session_id=session_id,
                assumption=data['assumption'],
                confidence=data.get('confidence', 0.5),
                domain=data.get('domain'),
                goal_id=goal_id,
                transaction_id=transaction_id,
            )
        elif ntype == 'decision':
            return db.log_project_decision(
                project_id=project_id, session_id=session_id,
                choice=data['choice'], rationale=data['rationale'],
                alternatives=data.get('alternatives'),
                reversibility=data.get('reversibility', 'exploratory'),
                confidence=data.get('confidence', 0.7),
                domain=data.get('domain'),
                goal_id=goal_id,
                transaction_id=transaction_id,
            )
        elif ntype == 'source':
            return db.add_reference_doc(
                project_id=project_id,
                doc_path=data.get('title', ''),
                doc_type=data.get('source_type'),
                description=data.get('description'),
            )
    except Exception as e:
        logger.warning(f"Failed to create {ntype} node '{node.get('ref')}': {e}")
    return None


def _wire_edges(db, edges: list[dict], ref_map: dict[str, str]) -> int:
    """Wire edges between created artifacts. Returns count of edges wired."""
    wired = 0
    for edge in edges:
        from_id = ref_map.get(edge['from'], edge['from'])
        to_id = ref_map.get(edge['to'], edge['to'])
        relation = edge['relation']

        # Store edge in the 'from' artifact's data column as JSON
        try:
            _store_edge(db, from_id, to_id, relation)
            wired += 1
        except Exception as e:
            logger.debug(f"Failed to wire edge {edge}: {e}")

    return wired


def _store_edge(db, from_id: str, to_id: str, relation: str):
    """Store an edge relationship in the artifact's data column."""
    if not db.conn:
        return

    cursor = db.conn.cursor()

    # Try each artifact table to find the 'from' artifact
    tables = [
        'project_findings', 'project_unknowns', 'project_dead_ends',
        'project_assumptions', 'project_decisions', 'mistakes_made',
    ]
    id_columns = {
        'project_findings': 'finding_id',
        'project_unknowns': 'unknown_id',
        'project_dead_ends': 'dead_end_id',
        'project_assumptions': 'assumption_id',
        'project_decisions': 'decision_id',
        'mistakes_made': 'mistake_id',
    }

    for table in tables:
        id_col = id_columns[table]
        cursor.execute(f"SELECT data FROM {table} WHERE {id_col} = ?", (from_id,))
        row = cursor.fetchone()
        if row is not None:
            existing_data = {}
            if row[0]:
                try:
                    existing_data = json.loads(row[0])
                except (json.JSONDecodeError, TypeError):
                    pass

            edges_list = existing_data.get('edges', [])
            edges_list.append({'to': to_id, 'relation': relation})
            existing_data['edges'] = edges_list

            cursor.execute(
                f"UPDATE {table} SET data = ? WHERE {id_col} = ?",
                (json.dumps(existing_data), from_id),
            )
            db.conn.commit()
            return


def _auto_embed_node(node: dict, artifact_id: str, context: dict):
    """Auto-embed a created node to Qdrant (non-fatal)."""
    try:
        from empirica.core.qdrant.memory import embed_single_memory_item
        ntype = node['type']
        data = node['data']

        # Build text from type-specific fields
        if ntype == 'finding':
            text = data['finding']
        elif ntype == 'unknown':
            text = data['unknown']
        elif ntype == 'dead_end':
            text = f"{data['approach']}: {data['why_failed']}"
        elif ntype == 'mistake':
            text = f"{data['mistake']}: {data['why_wrong']}"
        elif ntype == 'assumption':
            text = data['assumption']
        elif ntype == 'decision':
            text = f"{data['choice']}: {data['rationale']}"
        else:
            return

        embed_single_memory_item(
            project_id=context['project_id'],
            item_id=artifact_id,
            text=text,
            item_type=ntype,
            session_id=context['session_id'],
        )
    except Exception:
        pass  # Qdrant embedding is non-critical


def _read_graph_input(args) -> dict | None:
    """Read and validate graph JSON from stdin or file."""
    from empirica.cli.cli_utils import parse_json_safely

    if hasattr(args, 'config') and args.config:
        if args.config == '-':
            raw = sys.stdin.read()
        else:
            with open(args.config) as f:
                raw = f.read()
    else:
        raw = sys.stdin.read()

    graph = parse_json_safely(raw)
    if not graph:
        print(json.dumps({"ok": False, "error": "Invalid JSON input"}))
        return None

    errors = _validate_graph(graph)
    if errors:
        print(json.dumps({"ok": False, "errors": errors}))
        return None

    return graph


def _resolve_graph_context(graph: dict, args, db) -> dict | None:
    """Resolve session/project/transaction context for graph operations."""
    from empirica.utils.session_resolver import InstanceResolver as R

    session_id = graph.get('session_id') or getattr(args, 'session_id', None)
    if not session_id:
        try:
            ctx = R.context()
            session_id = ctx.get('empirica_session_id')
        except Exception:
            pass

    project_id = graph.get('project_id') or getattr(args, 'project_id', None)
    if not project_id and session_id:
        session = db.get_session(session_id)
        if session:
            project_id = session.get('project_id')

    if not session_id or not project_id:
        print(json.dumps({"ok": False, "error": "Could not resolve session_id or project_id"}))
        return None

    transaction_id = graph.get('transaction_id')
    if not transaction_id:
        try:
            ctx = R.context()
            transaction_id = ctx.get('transaction_id')
        except Exception:
            pass

    return {
        'session_id': session_id,
        'project_id': project_id,
        'goal_id': graph.get('goal_id'),
        'transaction_id': transaction_id,
    }


def handle_log_artifacts_command(args):
    """Handle log-artifacts command: batch artifact logging with graph format."""
    try:
        from empirica.data.session_database import SessionDatabase

        graph = _read_graph_input(args)
        if not graph:
            return 1

        db = SessionDatabase()
        context = _resolve_graph_context(graph, args, db)
        if not context:
            db.close()
            return 1

        # Sort nodes by creation order
        nodes = graph.get('nodes', [])
        sorted_nodes = sorted(nodes, key=lambda n: CREATION_ORDER.index(n.get('type', 'finding'))
                              if n.get('type') in CREATION_ORDER else 99)

        # Create nodes
        ref_map: dict[str, str] = {}
        created_errors: list[str] = []

        for node in sorted_nodes:
            artifact_id = _create_node(db, node, context)
            if artifact_id:
                ref_map[node['ref']] = artifact_id
                _auto_embed_node(node, artifact_id, context)
            else:
                created_errors.append(f"Failed to create {node['type']} '{node['ref']}'")

        # Wire edges
        edges = graph.get('edges', [])
        edges_wired = _wire_edges(db, edges, ref_map) if edges else 0

        db.close()

        # Git notes (non-fatal)
        try:
            # Just trigger a breadcrumb write for the batch
            import subprocess
            subprocess.run(
                ['git', 'notes', '--ref=breadcrumbs', 'append', '-m',
                 json.dumps({"batch_log": len(ref_map), "edges": edges_wired})],
                capture_output=True, timeout=5, check=False,
            )
        except Exception:
            pass

        result = {
            "ok": True,
            "created": ref_map,
            "nodes_created": len(ref_map),
            "edges_wired": edges_wired,
            "errors": created_errors,
        }
        print(json.dumps(result, indent=2))
        return 0

    except Exception as e:
        handle_cli_error(e, "Log artifacts", getattr(args, 'verbose', False))
        return 1


def handle_resolve_artifacts_command(args):
    """Handle resolve-artifacts command: batch resolution of open artifacts."""
    try:
        from empirica.cli.cli_utils import parse_json_safely
        from empirica.data.session_database import SessionDatabase

        # Parse input
        if hasattr(args, 'config') and args.config:
            if args.config == '-':
                raw = sys.stdin.read()
            else:
                with open(args.config) as f:
                    raw = f.read()
        else:
            raw = sys.stdin.read()

        resolutions = parse_json_safely(raw)
        if not resolutions:
            print(json.dumps({"ok": False, "error": "Invalid JSON input"}))
            return 1

        db = SessionDatabase()
        if not db.conn:
            print(json.dumps({"ok": False, "error": "No database connection"}))
            return 1

        resolved_count = 0
        resolution_errors: list[str] = []
        items = resolutions.get('resolutions', resolutions.get('items', []))

        for item in items:
            artifact_type = item.get('type')
            artifact_id = item.get('id')
            resolution = item.get('resolution', item.get('resolved_by', ''))

            if not artifact_id or not artifact_type:
                resolution_errors.append("Missing 'id' or 'type' in resolution item")
                continue

            try:
                if artifact_type == 'unknown':
                    cursor = db.conn.cursor()
                    cursor.execute(
                        "UPDATE project_unknowns SET is_resolved = 1, resolved_by = ?, "
                        "resolved_timestamp = datetime('now') WHERE unknown_id LIKE ?",
                        (resolution, f"{artifact_id}%"),
                    )
                    if cursor.rowcount > 0:
                        resolved_count += 1
                    else:
                        resolution_errors.append(f"Unknown '{artifact_id}' not found")

                elif artifact_type == 'assumption':
                    cursor = db.conn.cursor()
                    cursor.execute(
                        "UPDATE project_assumptions SET is_verified = 1, "
                        "verified_by = ? WHERE assumption_id LIKE ?",
                        (resolution, f"{artifact_id}%"),
                    )
                    if cursor.rowcount > 0:
                        resolved_count += 1
                    else:
                        resolution_errors.append(f"Assumption '{artifact_id}' not found")

                elif artifact_type == 'goal':
                    reason = item.get('reason', resolution)
                    cursor = db.conn.cursor()
                    cursor.execute(
                        "UPDATE project_goals SET is_completed = 1, status = 'completed', "
                        "completed_reason = ? WHERE goal_id LIKE ?",
                        (reason, f"{artifact_id}%"),
                    )
                    if cursor.rowcount > 0:
                        resolved_count += 1
                    else:
                        resolution_errors.append(f"Goal '{artifact_id}' not found")

                else:
                    resolution_errors.append(f"Unsupported resolution type: '{artifact_type}'")

            except Exception as e:
                resolution_errors.append(f"Error resolving {artifact_type} '{artifact_id}': {e}")

        db.conn.commit()
        db.close()

        result = {
            "ok": True,
            "resolved": resolved_count,
            "errors": resolution_errors,
        }
        print(json.dumps(result, indent=2))
        return 0

    except Exception as e:
        handle_cli_error(e, "Resolve artifacts", getattr(args, 'verbose', False))
        return 1
