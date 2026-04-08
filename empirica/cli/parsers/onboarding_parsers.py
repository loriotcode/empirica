"""Onboarding command parsers."""


def add_onboarding_parsers(subparsers):
    """Add onboarding command parsers"""
    # Onboard command - interactive introduction to Empirica
    onboard_parser = subparsers.add_parser(
        'onboard',
        help='Interactive introduction to Empirica (recommended for first-time users)'
    )
    onboard_parser.add_argument(
        '--ai-id',
        default='claude-code',
        help='AI identifier (optional, default: claude-code)'
    )

    # Setup Claude Code command - configure Claude Code integration
    setup_cc_parser = subparsers.add_parser(
        'setup-claude-code',
        help='Configure Claude Code integration (hooks, CLAUDE.md, MCP server)',
        description="""
Configure Claude Code integration for Empirica. This command:

1. Installs the empirica plugin to ~/.claude/plugins/local/
2. Configures CLAUDE.md system prompt in ~/.claude/
3. Sets up hooks in settings.json:
   - Sentinel gate (blocks praxic tools until CHECK passes)
   - Pre/post compact (epistemic state persistence)
   - Session lifecycle (init, end, subagent tracking)
4. Configures MCP server in mcp.json (installs empirica-mcp if needed)

Run this after 'brew install empirica' or 'pip install empirica'.
        """
    )
    setup_cc_parser.add_argument(
        '--force',
        action='store_true',
        help='Reinstall plugin even if it already exists'
    )
    setup_cc_parser.add_argument(
        '--skip-mcp',
        action='store_true',
        help='Skip MCP server installation and configuration'
    )
    setup_cc_parser.add_argument(
        '--skip-claude-md',
        action='store_true',
        help='Skip CLAUDE.md installation (keep existing system prompt)'
    )
    setup_cc_parser.add_argument(
        '--full-prompt',
        action='store_true',
        help='Use full system prompt instead of lean core default (lean loads skills on demand)'
    )
    setup_cc_parser.add_argument(
        '--output',
        choices=['human', 'json'],
        default='human',
        help='Output format (default: human)'
    )
    setup_cc_parser.add_argument(
        '--verbose',
        action='store_true',
        help='Show detailed output'
    )

    # Diagnose command - check Empirica + Claude Code integration health
    diagnose_parser = subparsers.add_parser(
        'diagnose',
        help='Check Empirica + Claude Code integration health (run this when statusline isn\'t showing)',
        description="""
Walks through the Empirica + Claude Code integration step-by-step and
reports PASS / FAIL / WARN with an actionable hint per check. Designed
for the recurring "I installed it but the statusline isn't showing"
class of question.

Checks:
  - Python version
  - empirica CLI on PATH
  - Claude Code config dir (~/.claude/ or $CLAUDE_CONFIG_DIR)
  - Plugin files installed in ~/.claude/plugins/local/empirica/
  - settings.json present and valid JSON
  - statusLine block configured
  - Hooks registered (PreToolUse, PreCompact, PostCompact, SessionStart)
  - Local marketplace registered
  - Statusline script runnable + produces output
  - Active session in current project

Exit codes:
  0  - all checks passed
  1  - one or more FAIL checks
  2  - one or more WARN checks (no FAIL)
        """
    )
    diagnose_parser.add_argument(
        '--output',
        choices=['human', 'json'],
        default='human',
        help='Output format (default: human)'
    )
