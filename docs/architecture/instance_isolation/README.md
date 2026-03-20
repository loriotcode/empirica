# Instance Isolation Documentation

Multiple Claude/AI instances can run simultaneously in different terminals or tmux panes.
This folder documents how Empirica keeps them isolated.

## Which doc do I need?

| You are... | Read this |
|------------|-----------|
| Understanding the architecture | [ARCHITECTURE.md](./ARCHITECTURE.md) |
| Using Claude Code (Anthropic's CLI) | [CLAUDE_CODE.md](./CLAUDE_CODE.md) |
| Building an MCP server or custom CLI | [MCP_AND_CLI.md](./MCP_AND_CLI.md) |
| Debugging isolation issues | [KNOWN_ISSUES.md](./KNOWN_ISSUES.md) |

## Quick Summary

**Problem:** Multiple AI instances share `ai_id=claude-code`. CWD gets reset unpredictably. Which project am I working on?

**Solution:** File-based isolation + `InstanceResolver` class (v1.6.14+):

```python
from empirica.utils.session_resolver import InstanceResolver as R
R.project_path()      # Resolves active project
R.session_id()        # Resolves active Empirica session
R.instance_suffix()   # Sanitized suffix for file naming
```

**Resolution priority:** `instance_projects` (P0) → `active_work_{uuid}` (P1) → `active_work.json` (P2) → None (fail explicitly).

## Key Principles

1. **Hooks write, everything else reads.** Exception: `project-switch` writes signals.
2. **`instance_projects` is authoritative** — writable by both hooks and CLI.
3. **Session-init on resume** — anchor files updated for new terminals automatically.
4. **`session-init` fires on both `startup` and `resume`** — handles new terminals for continued conversations.
5. **Suffix sanitization** — `:` → `_`, `%` removed. All file operations use `_get_instance_suffix()`.
6. **Fail explicitly** — return None rather than silently using wrong project.
