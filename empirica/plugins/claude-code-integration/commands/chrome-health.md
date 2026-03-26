---
description: "Check Chrome MCP connection health and set up monitoring: /chrome-health [monitor|status|stop]"
allowed-tools: ["Bash(python3 *)", "Bash(pkill *)", "Bash(kill *)", "Bash(rm *)", "CronCreate", "CronList", "CronDelete", "Read"]
---

# /chrome-health — Chrome MCP Health Monitor

**Arguments:** `status` (default) | `monitor` | `stop`

Chrome MCP connections die silently in long-lived Empirica sessions (known bug: anthropics/claude-code#25956).
This command detects dead connections and sets up periodic monitoring.

## For `/chrome-health` or `/chrome-health status`:

Run the health check script and report results:

```bash
python3 ~/.claude/plugins/local/empirica/scripts/chrome_health_check.py
```

**Interpret the output:**

| Status | Meaning | Action |
|--------|---------|--------|
| `healthy` | Chrome MCP is connected and working | No action needed |
| `degraded` | Warnings present (stale sockets, multiple processes) | Clean up stale resources |
| `dead` | Chrome MCP connection is broken | Follow recovery instructions in output |
| `unknown` | Cannot determine status (Chrome may not be enabled) | Check if session was started with `--chrome` |

If status is `dead`, display the recovery instructions from the JSON output.
If status is `degraded`, offer to clean up stale resources automatically.

## For `/chrome-health monitor`:

Set up a recurring health check using CronCreate. This polls every 10 minutes while the session is idle.

1. First check if there's already a monitoring cron job:
   - Use `CronList` to see existing jobs
   - If a chrome-health job exists, inform the user and skip

2. Create the monitoring job:
   - Use `CronCreate` with cron `"*/10 * * * *"` and prompt:
     ```
     Run the Chrome MCP health check silently. Execute: python3 ~/.claude/plugins/local/empirica/scripts/chrome_health_check.py
     If the status is "dead" or "degraded", warn me immediately with the details and recovery steps.
     If the status is "healthy", say nothing — just continue silently.
     ```
   - Tell the user: "Chrome MCP health monitoring active. Checks every 10 minutes. Auto-expires after 3 days (CronCreate limit). Run `/chrome-health stop` to cancel."

## For `/chrome-health stop`:

1. Use `CronList` to find the chrome-health monitoring job
2. Use `CronDelete` with the job ID to cancel it
3. Confirm: "Chrome MCP health monitoring stopped."

## Recovery Workflow

When health check reports `dead`:

1. **Show the user** the exact recovery steps from the health check output
2. **Explain** that this requires a session restart because Claude Code can't respawn the MCP server internally
3. **Reassure** that Empirica's `project-bootstrap` will recover epistemic context after restart
4. **Suggest** running `/chrome-health monitor` after the fresh session starts to prevent future silent deaths

## Quick Recovery Commands

If the user wants to attempt recovery without restarting:

```bash
# Kill stale native hosts
pkill -f "chrome-native-host"

# Clear stale sockets
rm -rf /tmp/claude-mcp-browser-bridge-$(whoami)/
```

Then tell the user to run `/chrome` to attempt reconnection. If that doesn't work, a full session restart is needed.
