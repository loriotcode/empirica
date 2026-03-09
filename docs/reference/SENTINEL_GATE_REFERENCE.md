# Sentinel Gate Reference

**Status:** AUTHORITATIVE
**Source:** `empirica/plugins/claude-code-integration/hooks/sentinel-gate.py`
**Audience:** Developers, not AI agents (see [Sentinel Constitution](../architecture/SENTINEL_CONSTITUTION.md) Principle II: Measurement Opacity)
**Last Updated:** 2026-03-09

---

## Overview

The Sentinel Gate is a `PreToolUse` hook that implements least-privilege access control for AI tool usage. It classifies every tool call as **noetic** (read/investigate) or **praxic** (write/execute) and gates praxic actions until the AI demonstrates sufficient epistemic readiness.

Design principle: **iptables for cognition** — default deny, explicit allow.

---

## Decision Flow

```
Tool Call Arrives
│
├─ Rule 1: Tool in NOETIC_TOOLS? ──────────────────── → ALLOW (noetic)
├─ Rule 2: Bash + is_safe_bash_command()? ─────────── → ALLOW (safe bash)
├─ Rule 2b: Write/Edit + is_plan_file()? ──────────── → ALLOW (plan file)
│
│  ── Everything below is PRAXIC ──
│
├─ Rule 3a: No active_work file for session? ──────── → ALLOW (subagent exemption)
├─ Empirica paused (sentinel_paused file)? ────────── → ALLOW (off-record)
├─ Sentinel disabled (file flag or env var)? ──────── → ALLOW (disabled)
│
│  ── Authorization checks (requires DB) ──
│
├─ Optional: Bootstrap required? ──────────────────── → DENY if no project_id
├─ No PREFLIGHT found?
│   ├─ Safe bash / transition command? ────────────── → ALLOW (pre-transaction)
│   └─ Otherwise ─────────────────────────────────── → DENY (no transaction)
├─ Project context changed? ───────────────────────── → DENY (re-PREFLIGHT)
├─ POSTFLIGHT exists after PREFLIGHT? (loop closed)
│   ├─ Safe bash / toggle / transition? ───────────── → ALLOW (inter-transaction)
│   └─ Otherwise ──────────────────────────────────── → DENY (loop closed)
│
│  ── Readiness evaluation ──
│
├─ Anti-gaming: Previous INVESTIGATE + no findings? ── → DENY (show evidence)
├─ AUTO-PROCEED: know ≥ threshold, unc ≤ threshold? ─ → ALLOW
├─ No CHECK found? ────────────────────────────────── → DENY (need CHECK)
├─ CHECK before PREFLIGHT? ────────────────────────── → DENY (stale CHECK)
├─ Rushed (<30s) + no findings/unknowns? ──────────── → DENY (rushed)
├─ CHECK decision = "investigate"? ────────────────── → DENY (investigate)
├─ Optional: CHECK expired (>30min)? ──────────────── → DENY (expired)
├─ Optional: Compact after CHECK? ─────────────────── → DENY (compacted)
├─ CHECK vectors pass readiness gate? ─────────────── → ALLOW
└─ Otherwise ──────────────────────────────────────── → DENY (insufficient)
```

### Fail-Open Design

If the Sentinel crashes (import error, DB lock, unexpected exception), the tool call is **allowed** with a warning. Work must never be blocked by measurement failure (Constitution Principle VIII).

---

## Tool Classification

### Noetic Tools (Always Allowed)

```python
NOETIC_TOOLS = {
    'Read', 'Glob', 'Grep', 'LSP',       # File inspection
    'WebFetch', 'WebSearch',              # Web research
    'Task', 'TaskOutput',                 # Agent delegation
    'TodoWrite',                          # Planning
    'AskUserQuestion',                    # User interaction
    'Skill',                              # Skill invocation
    'KillShell',                          # Process management (cleanup)
}
```

### Plan File Writes (Noetic Exception)

Writes/edits to `~/.claude/plans/` are classified as noetic — planning is investigation, not execution.

Detection: `is_plan_file()` checks if `file_path` resolves to a path containing `/.claude/plans/`.

### Safe Bash Commands (Noetic)

Read-only shell operations classified by prefix matching:

| Category | Prefixes |
|----------|----------|
| **File inspection** | `cat`, `head`, `tail`, `less`, `more`, `ls`, `dir`, `tree`, `file`, `stat`, `wc`, `find`, `locate`, `which`, `type`, `whereis` |
| **Text search** | `grep`, `rg`, `ag`, `ack`, `sed -n`, `awk`, `jq` |
| **Git read** | `git status`, `git log`, `git diff`, `git show`, `git branch`, `git remote`, `git tag`, `git stash list`, `git blame`, `git ls-files`, `git ls-tree`, `git cat-file` |
| **GitHub CLI read** | `gh issue list/view/status`, `gh pr list/view/status/checks`, `gh repo view`, `gh release list/view`, `gh api` |
| **Environment** | `pwd`, `echo`, `printf`, `env`, `printenv`, `set`, `whoami`, `id`, `hostname`, `uname`, `date`, `cal` |
| **Package inspection** | `pip show/list/freeze/index`, `npm list/ls/view/info`, `cargo tree/metadata` |
| **Process inspection** | `ps`, `top -b -n 1`, `pgrep`, `jobs` |
| **Tmux inspection** | `tmux capture-pane/list-panes/list-windows/list-sessions/display-message/show-option` |
| **Disk inspection** | `df`, `du`, `mount`, `lsblk` |
| **Network inspection** | `curl`, `wget -O-`, `ping -c`, `dig`, `nslookup`, `host` |
| **Documentation** | `man`, `info`, `help` |
| **Testing** | `test`, `[` |

### Dangerous Shell Operators (Blocked)

```python
DANGEROUS_SHELL_OPERATORS = (';', '&&', '||', '`', '$(')
```

**Exception:** `&&` and `||` chains are allowed if **all segments** are individually safe (e.g., `cd /path && grep pattern file`).

### Safe Pipe Targets

When a pipe chain is detected, subsequent segments must start with a safe target:

```python
SAFE_PIPE_TARGETS = (
    'head', 'tail', 'wc', 'sort', 'uniq', 'grep', 'rg', 'awk', 'sed -n',
    'cut', 'tr', 'less', 'more', 'cat', 'xargs echo', 'tee /dev/stderr',
    'python3 -c', 'python -c', 'jq',
)
```

### Safe Redirections

Stderr suppression (`2>/dev/null`, `2>&1`, `>/dev/null`) is allowed. File redirections (`>`, `>>`, `<`) are blocked. Heredocs (`<<`) are allowed for safe commands.

### Safe SQLite Commands

Read-only SQLite access is allowed:
- Meta commands: `.schema`, `.tables`, `.dump`, `.indices`, `.mode`, `.headers`, `.help`, `.databases`
- SQL: `SELECT`, `PRAGMA`, `EXPLAIN`, `ANALYZE`

Write operations (`INSERT`, `UPDATE`, `DELETE`, `DROP`, `CREATE`, `ALTER`) are blocked.

### Empirica CLI Tiered Whitelist

Empirica commands use a two-tier system instead of a blanket whitelist (prevents prompt injection bypass):

**Tier 1 — Read-only (always safe):**
- Goal queries: `goals-list`, `goals-progress`, `goals-discover`, `goals-search`, `goals-get-stale`, `goals-ready`, `goal-analysis`
- Epistemic queries: `epistemics-list`, `epistemics-show`, `calibration-report`
- Project: `project-bootstrap`, `project-search`, `project-switch`, `project-list`
- Session: `session-snapshot`
- Workspace: `workspace-overview`, `workspace-map`, `workspace-list`, `ecosystem-check`
- Lesson queries: `lesson-list`, `lesson-search`, `lesson-recommend`, `lesson-stats`
- Sentinel queries: `sentinel-status`, `sentinel-check`
- Other: `monitor`, `efficiency-report`, `docs-assess`, `issue-list`

**Tier 2 — State-changing (allowed because they ARE the epistemic workflow):**
- Transaction: `preflight-submit`, `check-submit`, `postflight-submit`
- Breadcrumbs: `finding-log`, `unknown-log`, `deadend-log`, `mistake-log`, `assumption-log`, `decision-log`, `source-add`, `refdoc-add`
- Goals: `goals-create`, `goals-complete`, `goals-add-subtask`, `goals-complete-subtask`, `goals-add-dependency`, `goals-resume`, `goals-claim`, `goals-mark-stale`, `goals-refresh`
- Session: `session-create`
- Project: `project-init`, `project-embed`
- Lessons: `lesson-create`, `lesson-load`, `lesson-path`, `lesson-replay-start`, `lesson-replay-end`, `lesson-embed`
- Other: `unknown-resolve`, `agent-spawn`, `investigate`, `artifacts-generate`, `sentinel-orchestrate`, `sentinel-load-profile`

---

## Transition Commands

After POSTFLIGHT closes a transaction, only specific commands are allowed to start a new cycle:

```python
TRANSITION_COMMANDS = (
    'cd ',                         # Directory change (project switch)
    'empirica session-create',     # New session
    'empirica project-bootstrap',  # Bootstrap new project context
    'empirica project-init',       # Initialize new project
    'empirica project-switch',     # Switch active project context
    'empirica project-list',       # List available projects
    'empirica preflight-submit',   # Start new epistemic cycle
    'git add',                     # Stage work from completed transaction
    'git commit',                  # Commit work from completed transaction
)
```

Transition detection handles piped commands (`echo '...' | empirica preflight-submit -`) and chained commands (`cd /path && empirica preflight-submit -`).

---

## Anti-Gaming Protections

### Rushed Assessment Detection

If CHECK arrives less than 30 seconds after PREFLIGHT **and** no findings or unknowns were logged in between, the Sentinel blocks with "Rushed assessment." This prevents the AI from gaming the gate by quickly submitting high-confidence vectors without investigation.

Configurable via `EMPIRICA_MIN_NOETIC_DURATION` env var (default: 30 seconds).

### INVESTIGATE Continuity

If the previous transaction's CHECK returned `investigate` and the AI opens a new transaction without logging any findings, the Sentinel blocks. This prevents gaming by creating a fresh transaction with high confidence to bypass the investigate decision.

### Raw Vectors Only

The Sentinel evaluates **raw (uncorrected) vectors** from PREFLIGHT/CHECK. Calibration corrections are feedback for the AI to internalize — they are never applied silently to gating decisions. This is intentional and not controlled by `EMPIRICA_CALIBRATION_FEEDBACK`.

---

## Autonomy Calibration Loop

### Tool Count Tracking

Every `PreToolUse` event increments `tool_call_count` in the active transaction file. Counts are split:
- `noetic_tool_calls`: Tools classified as noetic (NOETIC_TOOLS, safe Bash, plan files)
- `praxic_tool_calls`: Everything else

Phase-split counts feed into phase-weighted calibration at POSTFLIGHT.

### Nudge Thresholds

Based on `avg_turns` (rolling average from last 20 POSTFLIGHTs):

| Ratio | Level | Message |
|-------|-------|---------|
| >= 1.0x | Info | "Past average. Natural POSTFLIGHT point." |
| >= 1.5x | Warning | "Consider POSTFLIGHT soon." |
| >= 2.0x | Strong | "POSTFLIGHT strongly recommended." |

Nudges appear in `permissionDecisionReason` on allowed tool calls. They are informational — the AI decides when to POSTFLIGHT.

### Pre-Transaction Monitoring

When no transaction is open, the Sentinel counts tool calls in a separate counter file (`pre_tx_calls_{instance_id}.json`). Nudges:
- 5+ calls: "Consider submitting PREFLIGHT to begin measured work."
- 10+ calls: "STRONGLY RECOMMENDED: Submit PREFLIGHT now — this work is unmeasured."

---

## Subagent Exemption

Subagents (spawned via `Task` tool) bypass the Sentinel gate. Detection: if no `active_work_{session_id}.json` exists for the calling session, it's a subagent.

Rationale: The parent's CHECK already authorized the spawn. Double-gating is redundant (see CASCADE Exemption in CANONICAL_CORE.md).

Subagent tool calls are counted separately and added to the parent's `delegated_tool_calls` by the `SubagentStop` hook.

---

## Pause/Resume (Off-Record Mode)

### Pause Files

| File | Scope | Precedence |
|------|-------|------------|
| `~/.empirica/sentinel_paused_{instance_id}` | Per-instance | Checked first |
| `~/.empirica/sentinel_paused` | Global (all instances) | Fallback |

Instance ID: `tmux_{pane_number}` for tmux users, `{tty}` for non-tmux, or None.

### Toggle Detection

The Sentinel detects pause/unpause commands (writing or removing `sentinel_paused` files) and self-exempts — these commands are allowed even when the loop is closed. This prevents a chicken-and-egg problem where the Sentinel blocks its own toggle.

### Sentinel Disable

Two mechanisms to disable the Sentinel entirely:

| Mechanism | How | Priority | Requires restart? |
|-----------|-----|----------|-------------------|
| File flag | Write `false` to `~/.empirica/sentinel_enabled` | Higher | No |
| Env var | `EMPIRICA_SENTINEL_LOOPING=false` | Lower | Yes |

---

## Instance Isolation

Multi-Claude support via instance-specific files:

```
~/.empirica/active_transaction_{instance_id}.json  # Per-instance transaction
~/.empirica/sentinel_paused_{instance_id}          # Per-instance pause
~/.empirica/pre_tx_calls_{instance_id}.json        # Per-instance pre-tx counter
```

Instance ID resolution priority:
1. `TMUX_PANE` environment variable (available in hooks)
2. `tty` command output (fallback for non-tmux)

---

## Project Root Resolution

```
resolve_project_root(claude_session_id)
│
├─ get_active_project_path(claude_session_id)  [from project_resolver.py]
│   ├─ Priority 0: active_work_{session_id}.json → project_path
│   ├─ Priority 1: active_transaction_{instance}.json → infer from path
│   └─ Priority 2: instance_projects mapping
│
├─ Check .empirica/ exists under resolved path
│
└─ Fallback: find_empirica_package() for import path only
   (NOT for project resolution — no CWD fallback)
```

---

## Environment Variables

| Variable | Default | Effect |
|----------|---------|--------|
| `EMPIRICA_SENTINEL_LOOPING` | `true` | `false` disables Sentinel entirely |
| `EMPIRICA_SENTINEL_MODE` | `auto` | `observer` = log only, `controller`/`auto` = actively block |
| `EMPIRICA_SENTINEL_REQUIRE_BOOTSTRAP` | `false` | Require `project-bootstrap` before praxic actions |
| `EMPIRICA_SENTINEL_COMPACT_INVALIDATION` | `false` | Invalidate CHECK after context compaction |
| `EMPIRICA_SENTINEL_CHECK_EXPIRY` | `false` | Enable 30-minute CHECK expiry |
| `EMPIRICA_MIN_NOETIC_DURATION` | `30` | Minimum seconds between PREFLIGHT and CHECK |
| `EMPIRICA_CALIBRATION_FEEDBACK` | (not consumed here) | Controls calibration feedback in workflow output, NOT in gate logic |

---

## Response Format

The Sentinel outputs JSON to stdout in Claude Code's expected hook format:

```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "allow|deny",
    "permissionDecisionReason": "Reason text | optional autonomy nudge"
  },
  "suppressOutput": true  // Only on allow without nudge
}
```

- **Allow without nudge:** Output suppressed (no user-visible noise)
- **Allow with nudge:** Output shown (autonomy nudge visible to AI)
- **Deny:** Output shown with reason

---

## Related Documents

| Document | Relationship |
|----------|-------------|
| [Sentinel Constitution](../architecture/SENTINEL_CONSTITUTION.md) | Governance principles that constrain this code |
| [Sentinel Architecture](../architecture/SENTINEL_ARCHITECTURE.md) | Higher-level architecture (orchestrator, compliance gates) |
| [Phase-Aware Calibration](../architecture/PHASE_AWARE_CALIBRATION.md) | How phase-split tool counts feed calibration |
| [CASCADE Workflow](api/cascade_workflow.md) | PREFLIGHT/CHECK/POSTFLIGHT phase definitions |
| [Environment Variables](ENVIRONMENT_VARIABLES.md) | Full env var reference |
| [Configuration Reference](CONFIGURATION_REFERENCE.md) | File-based configuration |
