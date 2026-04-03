#!/usr/bin/env python3
"""
Empirica MCP Server — Thin CLI wrapper for AI agent environments.

Provides MCP tools that route to `empirica` CLI commands via subprocess.
No epistemic middleware — gating is handled by the Sentinel (hooks) in
Claude Code, or self-enforced on other platforms.

Architecture:
- Table-driven: TOOL_REGISTRY maps tool names → CLI commands + params
- All commands run with stdin=DEVNULL and timeout (no hanging)
- Graceful: if CLI not found, returns clear error
- Stateless: no session state in the server itself

Version: 1.7.5
"""

import argparse
import asyncio
import json
import logging
import os
import shutil
import subprocess
from pathlib import Path

from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server

logger = logging.getLogger(__name__)

# CLI resolution
EMPIRICA_CLI = shutil.which("empirica")
if not EMPIRICA_CLI:
    for path in [
        Path.home() / ".local" / "bin" / "empirica",
        Path("/usr/local/bin/empirica"),
    ]:
        if path.exists():
            EMPIRICA_CLI = str(path)
            break

CLI_TIMEOUT = int(os.environ.get("EMPIRICA_MCP_TIMEOUT", "30"))
MAX_OUTPUT = 30000

# =============================================================================
# Tool Registry — single source of truth for tool→CLI mapping
# =============================================================================
# Each entry: (cli_command, {param_name: cli_flag}, [required_params], description)
# Params with None as flag are positional or handled specially.

TOOL_REGISTRY: dict[str, dict] = {
    # --- Session lifecycle ---
    "session_create": {
        "cli": "session-create",
        "params": {"ai_id": "--ai-id", "session_type": "--session-type", "project_path": "--project-path"},
        "required": ["ai_id"],
        "desc": "Create new Empirica session",
    },
    "project_bootstrap": {
        "cli": "project-bootstrap",
        "params": {"ai_id": "--ai-id", "session_id": "--session-id", "depth": "--depth"},
        "required": [],
        "desc": "Load project context (findings, goals, unknowns, calibration)",
    },
    "session_snapshot": {
        "cli": "session-snapshot",
        "params": {"session_id": "--session-id"},
        "required": ["session_id"],
        "desc": "Create snapshot of current session state",
    },
    "resume_previous_session": {
        "cli": "sessions-resume",
        "params": {"session_id": "--session-id"},
        "required": ["session_id"],
        "desc": "Resume a previous session",
    },

    # --- CASCADE workflow ---
    "submit_preflight_assessment": {
        "cli": "preflight-submit",
        "params": {},
        "required": ["session_id", "vectors"],
        "desc": "Submit PREFLIGHT self-assessment (13 vectors 0.0-1.0)",
        "stdin_json": True,  # Sends full arguments as JSON to stdin
    },
    "submit_check_assessment": {
        "cli": "check-submit",
        "params": {},
        "required": ["session_id", "vectors"],
        "desc": "Submit CHECK gate assessment",
        "stdin_json": True,
    },
    "submit_postflight_assessment": {
        "cli": "postflight-submit",
        "params": {},
        "required": ["session_id", "vectors"],
        "desc": "Submit POSTFLIGHT assessment — closes transaction, triggers grounded verification",
        "stdin_json": True,
    },

    # --- Noetic artifacts ---
    "finding_log": {
        "cli": "finding-log",
        "params": {"finding": "--finding", "impact": "--impact", "session_id": "--session-id",
                   "goal_id": "--goal-id", "subtask_id": "--subtask-id", "project_id": "--project-id",
                   "subject": "--subject", "scope": "--scope",
                   "entity_type": "--entity-type", "entity_id": "--entity-id", "via": "--via"},
        "required": ["finding"],
        "desc": "Log a finding (what was learned)",
    },
    "unknown_log": {
        "cli": "unknown-log",
        "params": {"unknown": "--unknown", "session_id": "--session-id",
                   "goal_id": "--goal-id", "subtask_id": "--subtask-id", "project_id": "--project-id",
                   "subject": "--subject", "scope": "--scope",
                   "entity_type": "--entity-type", "entity_id": "--entity-id", "via": "--via"},
        "required": ["unknown"],
        "desc": "Log an unknown (what needs investigation)",
    },
    "deadend_log": {
        "cli": "deadend-log",
        "params": {"approach": "--approach", "why_failed": "--why-failed", "session_id": "--session-id",
                   "goal_id": "--goal-id", "subtask_id": "--subtask-id", "project_id": "--project-id",
                   "subject": "--subject", "scope": "--scope",
                   "entity_type": "--entity-type", "entity_id": "--entity-id", "via": "--via"},
        "required": ["approach", "why_failed"],
        "desc": "Log a dead-end (approach that didn't work)",
    },
    "mistake_log": {
        "cli": "mistake-log",
        "params": {"mistake": "--mistake", "why_wrong": "--why-wrong", "prevention": "--prevention",
                   "session_id": "--session-id", "goal_id": "--goal-id", "project_id": "--project-id",
                   "scope": "--scope", "entity_type": "--entity-type", "entity_id": "--entity-id"},
        "required": ["mistake", "why_wrong", "prevention"],
        "desc": "Log a mistake (error to avoid in future)",
    },
    "assumption_log": {
        "cli": "assumption-log",
        "params": {"assumption": "--assumption", "confidence": "--confidence", "domain": "--domain",
                   "session_id": "--session-id", "goal_id": "--goal-id", "project_id": "--project-id",
                   "entity_type": "--entity-type", "entity_id": "--entity-id", "via": "--via"},
        "required": ["assumption"],
        "desc": "Log an unverified assumption with confidence level",
    },
    "decision_log": {
        "cli": "decision-log",
        "params": {"choice": "--choice", "rationale": "--rationale", "alternatives": "--alternatives",
                   "reversibility": "--reversibility", "confidence": "--confidence", "domain": "--domain",
                   "session_id": "--session-id", "goal_id": "--goal-id", "project_id": "--project-id",
                   "entity_type": "--entity-type", "entity_id": "--entity-id", "via": "--via"},
        "required": ["choice", "rationale"],
        "desc": "Log a decision with rationale",
    },
    "source_add": {
        "cli": "source-add",
        "params": {"title": "--title", "source_url": "--source-url", "source_type": "--source-type",
                   "session_id": "--session-id"},
        "required": ["title", "source_url", "source_type"],
        "desc": "Add an epistemic source reference",
    },

    # --- Goals ---
    "goals_create": {
        "cli": "goals-create",
        "params": {"objective": "--objective", "session_id": "--session-id", "project_id": "--project-id"},
        "required": ["objective"],
        "desc": "Create a new goal",
    },
    "goals_list": {
        "cli": "goals-list",
        "params": {"session_id": "--session-id", "status": "--status"},
        "required": [],
        "desc": "List goals (optionally filter by status)",
    },
    "goals_complete": {
        "cli": "goals-complete",
        "params": {"goal_id": "--goal-id", "reason": "--reason"},
        "required": ["goal_id"],
        "desc": "Mark a goal as complete",
    },
    "goals_add_subtask": {
        "cli": "goals-add-subtask",
        "params": {"goal_id": "--goal-id", "description": "--description",
                   "importance": "--importance"},
        "required": ["goal_id", "description"],
        "desc": "Add a subtask to a goal",
    },
    "goals_complete_subtask": {
        "cli": "goals-complete-subtask",
        "params": {"task_id": "--task-id", "evidence": "--evidence"},
        "required": ["task_id"],
        "desc": "Mark a subtask as complete",
    },
    "goals_progress": {
        "cli": "goals-progress",
        "params": {"goal_id": "--goal-id"},
        "required": ["goal_id"],
        "desc": "Get goal progress details",
    },
    "goals_search": {
        "cli": "goals-search",
        "params": {"query": "--query", "status": "--status"},
        "required": ["query"],
        "desc": "Search goals by text",
    },
    "goals_ready": {
        "cli": "goals-ready",
        "params": {"session_id": "--session-id"},
        "required": [],
        "desc": "List goals ready for work (no blockers)",
    },

    # --- Unknowns ---
    "unknown_list": {
        "cli": "unknown-list",
        "params": {"session_id": "--session-id", "status": "--status"},
        "required": [],
        "desc": "List unknowns",
    },
    "unknown_resolve": {
        "cli": "unknown-resolve",
        "params": {"unknown_id": "--unknown-id", "resolved_by": "--resolved-by"},
        "required": ["unknown_id"],
        "desc": "Resolve an unknown",
    },

    # --- Search and memory ---
    "project_search": {
        "cli": "project-search",
        "params": {"task": "--task", "project_id": "--project-id", "type": "--type",
                   "limit": "--limit"},
        "required": ["task"],
        "desc": "Semantic search over project knowledge (Qdrant)",
    },
    "project_embed": {
        "cli": "project-embed",
        "params": {"project_id": "--project-id"},
        "required": [],
        "desc": "Embed project artifacts to Qdrant for semantic search",
    },

    # --- Calibration and state ---
    "calibration_report": {
        "cli": "calibration-report",
        "params": {"session_id": "--session-id", "grounded": "--grounded"},
        "required": [],
        "desc": "Get calibration report (self-referential and/or grounded)",
    },
    "assess_state": {
        "cli": "assess-state",
        "params": {"session_id": "--session-id"},
        "required": [],
        "desc": "Get current epistemic state assessment",
    },
    "profile_status": {
        "cli": "profile-status",
        "params": {},
        "required": [],
        "desc": "Show artifact counts, drift, calibration summary",
    },

    # --- Lessons ---
    "lesson_create": {
        "cli": "lesson-create",
        "params": {"name": "--name", "description": "--description", "domain": "--domain",
                   "content": "--content", "session_id": "--session-id"},
        "required": ["name", "description"],
        "desc": "Create a reusable lesson from experience",
    },
    "lesson_list": {
        "cli": "lesson-list",
        "params": {"domain": "--domain"},
        "required": [],
        "desc": "List available lessons",
    },
    "lesson_search": {
        "cli": "lesson-search",
        "params": {"query": "--query"},
        "required": ["query"],
        "desc": "Search lessons by text",
    },

    # --- Issues ---
    "issue_list": {
        "cli": "issue-list",
        "params": {"status": "--status", "severity": "--severity"},
        "required": [],
        "desc": "List auto-captured issues",
    },
    "issue_resolve": {
        "cli": "issue-resolve",
        "params": {"session_id": "--session-id", "issue_id": "--issue-id",
                   "resolution": "--resolution"},
        "required": ["session_id", "issue_id", "resolution"],
        "desc": "Resolve an auto-captured issue",
    },

    # --- Investigation ---
    "investigate": {
        "cli": "investigate",
        "params": {"question": "--question", "session_id": "--session-id", "depth": "--depth"},
        "required": ["question"],
        "desc": "Run structured investigation on a question",
    },

    # --- Handoff ---
    "handoff_create": {
        "cli": "handoff-create",
        "params": {"session_id": "--session-id", "format": "--format"},
        "required": [],
        "desc": "Create handoff report for session continuation",
    },

    # --- Workspace ---
    "workspace_overview": {
        "cli": "workspace-overview",
        "params": {},
        "required": [],
        "desc": "Show workspace overview (all tracked projects)",
    },
    "workspace_map": {
        "cli": "workspace-map",
        "params": {},
        "required": [],
        "desc": "Show knowledge map across projects",
    },

    # --- Monitor ---
    "monitor": {
        "cli": "monitor",
        "params": {"session_id": "--session-id"},
        "required": [],
        "desc": "Show real-time session monitoring dashboard",
    },

    # --- Checkpoint ---
    "checkpoint_create": {
        "cli": "checkpoint-create",
        "params": {"session_id": "--session-id", "message": "--message"},
        "required": [],
        "desc": "Create a git checkpoint with epistemic metadata",
    },
    "checkpoint_load": {
        "cli": "checkpoint-load",
        "params": {"checkpoint_id": "--checkpoint-id"},
        "required": ["checkpoint_id"],
        "desc": "Load a checkpoint",
    },

    # --- Docs ---
    "refdoc_add": {
        "cli": "refdoc-add",
        "params": {"title": "--title", "source_url": "--source-url", "doc_type": "--doc-type"},
        "required": ["title"],
        "desc": "Register a reference document",
    },

    # --- Misc ---
    "memory_compact": {
        "cli": "memory-compact",
        "params": {"session_id": "--session-id"},
        "required": [],
        "desc": "Compact session memory (deduplicate, prune stale)",
    },
    "efficiency_report": {
        "cli": "efficiency-report",
        "params": {"session_id": "--session-id"},
        "required": [],
        "desc": "Generate efficiency report for session",
    },
}

# =============================================================================
# Tool schemas — auto-generated from registry
# =============================================================================

# Param type hints for schema generation
_NUMERIC_PARAMS = {"impact", "confidence", "estimated_complexity", "limit"}
_BOOLEAN_PARAMS = {"grounded"}
_ENUM_PARAMS = {
    "reversibility": ["exploratory", "committal", "forced"],
    "scope": ["session", "project", "both"],
    "entity_type": ["project", "organization", "contact", "engagement"],
    "source_type": ["doc", "spec", "paper", "blog", "video", "code", "api", "other"],
    "status": ["in_progress", "completed", "stale", "abandoned"],
    "severity": ["low", "medium", "high", "critical"],
}


def _build_tool_schema(name: str, entry: dict) -> types.Tool:
    """Build MCP Tool schema from registry entry."""
    properties = {}
    for param in entry["params"]:
        if param in _NUMERIC_PARAMS:
            properties[param] = {"type": "number", "description": param.replace("_", " ").title()}
        elif param in _BOOLEAN_PARAMS:
            properties[param] = {"type": "boolean", "description": param.replace("_", " ").title()}
        elif param in _ENUM_PARAMS:
            properties[param] = {"type": "string", "enum": _ENUM_PARAMS[param]}
        else:
            properties[param] = {"type": "string"}

    # For stdin_json tools, add vectors and reasoning
    if entry.get("stdin_json"):
        properties["session_id"] = {"type": "string", "description": "Session UUID"}
        properties["vectors"] = {"type": "object", "description": "13 epistemic vectors (0.0-1.0)"}
        properties["reasoning"] = {"type": "string", "description": "Assessment reasoning"}
        if name == "submit_preflight_assessment":
            properties["task_context"] = {"type": "string", "description": "What you're working on"}
            properties["work_type"] = {"type": "string", "description": "code|infra|research|debug|docs|comms|design|audit"}
            properties["work_context"] = {"type": "string", "description": "greenfield|iteration|investigation|refactor"}
        if name == "submit_check_assessment":
            properties["decision"] = {"type": "string", "enum": ["proceed", "investigate"]}

    return types.Tool(
        name=name,
        description=entry["desc"],
        inputSchema={
            "type": "object",
            "properties": properties,
            "required": entry["required"],
        },
    )


# =============================================================================
# MCP Server
# =============================================================================

app = Server("empirica")


@app.list_tools()
async def list_tools() -> list[types.Tool]:
    """List all available tools."""
    tools = [_build_tool_schema(name, entry) for name, entry in TOOL_REGISTRY.items()]

    # Add the one stateless tool (no CLI needed)
    tools.append(types.Tool(
        name="get_empirica_introduction",
        description="Get introduction to the Empirica epistemic framework",
        inputSchema={"type": "object", "properties": {}},
    ))
    return tools


@app.call_tool(validate_input=False)
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    """Route tool calls to CLI."""

    # Stateless handler
    if name == "get_empirica_introduction":
        return [types.TextContent(type="text", text=json.dumps({
            "framework": "Empirica",
            "purpose": "Epistemic self-assessment and calibration for AI agents",
            "workflow": "PREFLIGHT → CHECK → work → POSTFLIGHT",
            "vectors": 13,
            "docs": "https://github.com/Nubaeon/empirica",
            "commands": sorted(TOOL_REGISTRY.keys()),
        }, indent=2))]

    # Registry lookup
    entry = TOOL_REGISTRY.get(name)
    if not entry:
        return [types.TextContent(type="text", text=json.dumps({
            "ok": False,
            "error": f"Unknown tool: {name}",
            "available": sorted(TOOL_REGISTRY.keys()),
        }, indent=2))]

    if not EMPIRICA_CLI:
        return [types.TextContent(type="text", text=json.dumps({
            "ok": False,
            "error": "empirica CLI not found. Install: pip install empirica",
        }, indent=2))]

    # Build command
    cmd = [EMPIRICA_CLI, entry["cli"], "--output", "json"]
    stdin_data = None

    if entry.get("stdin_json"):
        # CASCADE commands: send full JSON via stdin
        cmd.append("-")
        stdin_data = json.dumps(arguments).encode("utf-8")
    else:
        # Standard commands: map params to CLI flags
        for param, flag in entry["params"].items():
            value = arguments.get(param)
            if value is not None:
                if isinstance(value, bool):
                    if value:
                        cmd.append(flag)
                else:
                    cmd.extend([flag, str(value)])

    # Resolve working directory
    cwd = arguments.get("project_path")
    if not cwd:
        cwd = os.environ.get("EMPIRICA_WORKSPACE_ROOT")
    if not cwd:
        try:
            from empirica.utils.session_resolver import get_active_project_path
            cwd = get_active_project_path()
        except Exception:
            pass

    # Execute
    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(
            None,
            lambda: subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                input=stdin_data.decode("utf-8") if stdin_data else None,
                stdin=None if stdin_data else subprocess.DEVNULL,
                cwd=cwd,
                timeout=CLI_TIMEOUT,
            ),
        )
    except subprocess.TimeoutExpired:
        return [types.TextContent(type="text", text=json.dumps({
            "ok": False,
            "error": f"Command timed out ({CLI_TIMEOUT}s): {entry['cli']}",
        }, indent=2))]

    if result.returncode == 0:
        output = result.stdout or result.stderr or '{"ok": true}'
        if len(output) > MAX_OUTPUT:
            output = output[:MAX_OUTPUT] + f"\n\n⚠️ Truncated ({len(output)} chars)"
        return [types.TextContent(type="text", text=output)]
    else:
        return [types.TextContent(type="text", text=json.dumps({
            "ok": False,
            "error": result.stderr or result.stdout or "Command failed",
            "command": entry["cli"],
        }, indent=2))]


# =============================================================================
# Entry point
# =============================================================================

async def main():
    """Run MCP server via stdio."""
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


def run():
    """CLI entry point for empirica-mcp."""
    parser = argparse.ArgumentParser(description="Empirica MCP Server")
    parser.add_argument("--workspace", "-w", help="Project workspace root")
    args = parser.parse_args()

    if args.workspace:
        workspace = Path(args.workspace).expanduser().resolve()
        if workspace.exists():
            os.environ["EMPIRICA_WORKSPACE_ROOT"] = str(workspace)
            logger.info(f"Workspace: {workspace}")
    elif not os.environ.get("EMPIRICA_WORKSPACE_ROOT"):
        # Auto-detect from git root
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                capture_output=True, text=True, timeout=2, check=False,
            )
            if result.returncode == 0:
                git_root = Path(result.stdout.strip())
                if (git_root / ".empirica").exists():
                    os.environ["EMPIRICA_WORKSPACE_ROOT"] = str(git_root)
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

    asyncio.run(main())


if __name__ == "__main__":
    run()
