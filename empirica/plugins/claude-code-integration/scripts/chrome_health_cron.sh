#!/bin/bash
# Chrome MCP health cron wrapper
# Runs chrome_health_check.py and sends desktop notification if dead/degraded.
# Install: crontab -e → */10 * * * * ~/.claude/plugins/local/empirica/scripts/chrome_health_cron.sh
#
# Requires: notify-send (libnotify) for desktop notifications

SCRIPT_DIR="$(dirname "$(readlink -f "$0")")"
HEALTH_CHECK="$SCRIPT_DIR/chrome_health_check.py"
LOG_FILE="$HOME/.empirica/chrome_health.log"

# Only run if Claude Code is actually running
if ! pgrep -x "claude" > /dev/null 2>&1; then
    exit 0
fi

# Only run if there's a chrome-native-host (Chrome was enabled at some point)
if ! pgrep -f "chrome-native-host" > /dev/null 2>&1; then
    exit 0
fi

# Run health check
RESULT=$(python3 "$HEALTH_CHECK" 2>/dev/null)
EXIT_CODE=$?

# Log result
TIMESTAMP=$(date +"%Y-%m-%dT%H:%M:%S%z")
echo "$TIMESTAMP | exit=$EXIT_CODE | $(echo "$RESULT" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get("status","unknown"))' 2>/dev/null)" >> "$LOG_FILE"

# Notify on failure
if [ $EXIT_CODE -eq 2 ]; then
    # Dead — urgent notification
    STATUS=$(echo "$RESULT" | python3 -c 'import sys,json; d=json.load(sys.stdin); polls=d.get("checks",{}).get("bridge_polls",{}).get("empty_polls","?"); print(f"Chrome MCP DEAD ({polls} empty polls). Restart session with: claude --chrome")' 2>/dev/null)

    # Desktop notification (works on GNOME/KDE/etc)
    DISPLAY=:0 DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$(id -u)/bus" \
        notify-send -u critical "Empirica: Chrome MCP Dead" "$STATUS" 2>/dev/null

    # Also write to a flag file that hooks can check
    echo "{\"status\": \"dead\", \"detected_at\": \"$TIMESTAMP\"}" > "$HOME/.empirica/chrome_mcp_dead"

elif [ $EXIT_CODE -eq 1 ]; then
    # Degraded — warning
    DISPLAY=:0 DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$(id -u)/bus" \
        notify-send -u normal "Empirica: Chrome MCP Degraded" "Run /chrome-health for details" 2>/dev/null
fi

# Clean up flag file if healthy
if [ $EXIT_CODE -eq 0 ] && [ -f "$HOME/.empirica/chrome_mcp_dead" ]; then
    rm "$HOME/.empirica/chrome_mcp_dead"
fi

# Trim log to last 500 lines
if [ -f "$LOG_FILE" ] && [ "$(wc -l < "$LOG_FILE")" -gt 500 ]; then
    tail -200 "$LOG_FILE" > "$LOG_FILE.tmp" && mv "$LOG_FILE.tmp" "$LOG_FILE"
fi
