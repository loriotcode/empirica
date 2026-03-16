# Instance Isolation Architecture

Core concepts and file taxonomy for multi-instance isolation.

## The One Rule

> **Hooks write. Everything else reads.**

Hooks have full context (`claude_session_id` from stdin + `TMUX_PANE` from env).
CLI commands, Sentinel, statusline, and MCP are **readers** — they resolve project
context from files that hooks wrote.

The sole exception is **`project-switch`**, which writes to `instance_projects` and
`tty_sessions` as a **signal** to hooks. It cannot write `active_work` because it
doesn't know `claude_session_id`. This is fine — `instance_projects` is read first.

## File Taxonomy

### 1. Instance Projects (tmux) — PRIMARY IN TMUX

**Location:** `~/.empirica/instance_projects/tmux_N.json`
**Key:** `TMUX_PANE` environment variable (e.g., `%4` → `tmux_4`)
**Written by:** Hooks (session-init, post-compact) AND `project-switch` CLI
**Read by:** Everyone (hooks, CLI, statusline, Sentinel)

```json
{
  "project_path": "/home/user/my-project",
  "claude_session_id": "fad66571-1bde-4ee1-aa0d-e9d3dfd8e833",
  "empirica_session_id": "2bc1da78-2a28-4745-b75b-f021d563d819",
  "timestamp": "2026-02-13T01:00:00"
}
```

**Purpose:** Links tmux pane → project. **Most current source in tmux** because it's
the only file writable by both hooks AND the project-switch CLI, AND `TMUX_PANE` is
truly unique per pane.

**Non-tmux:** `instance_projects` files may also exist for `x11_N`, `term_N`, etc.
but these IDs are shared across Claude instances in the same terminal emulator.
They serve as **fallback for CLI commands** (which lack `claude_session_id`) but
are NOT authoritative when hooks have `claude_session_id` available. See
[Resolution Priority Chain](#resolution-priority-chain) below.

### 2. Active Work Files (Claude Code) — PRIMARY IN NON-TMUX

**Location:** `~/.empirica/active_work_{claude_session_id}.json`
**Key:** Claude Code conversation UUID (from hook stdin)
**Written by:** Hooks ONLY (session-init, post-compact)

```json
{
  "project_path": "/home/user/my-project",
  "folder_name": "my-project",
  "claude_session_id": "fad66571-1bde-4ee1-aa0d-e9d3dfd8e833",
  "empirica_session_id": "2bc1da78-2a28-4745-b75b-f021d563d819",
  "source": "post-compact",
  "timestamp": "2026-02-13T01:00:00"
}
```

**Purpose:** Links Claude conversation → project. **Primary source in non-TMUX
environments** because `claude_session_id` is truly unique per Claude Code session
(unlike `WINDOWID` which is shared across instances in the same terminal).

**Limitation:** Cannot be updated by `project-switch` CLI (CLI doesn't know
`claude_session_id`), so may be stale after a project-switch until the next
SessionStart hook fires. In tmux, `instance_projects` handles this gap.

### 3. TTY Sessions (CLI/MCP)

**Location:** `~/.empirica/tty_sessions/pts-N.json`
**Key:** TTY device name (from `tty` command)
**Written by:** CLI commands (session-create, project-switch)

```json
{
  "claude_session_id": null,
  "empirica_session_id": "2bc1da78-2a28-4745-b75b-f021d563d819",
  "project_path": "/home/user/my-project",
  "tty_key": "pts-6",
  "timestamp": "2026-02-06T16:18:42",
  "pid": 1900034
}
```

**Purpose:** Links terminal → project. Primary isolation for non-Claude-Code users (MCP, direct CLI).

### 4. Transaction Files (Per-Project)

**Location:** `{project}/.empirica/active_transaction_{instance_id}.json`
**Key:** `instance_id` (e.g., `tmux_4`, `term_pts_6`, `default`)
**Written by:** PREFLIGHT command

```json
{
  "transaction_id": "e04ad48e-3c2b-48ef-96be-5ebcf86746c6",
  "session_id": "2bc1da78-2a28-4745-b75b-f021d563d819",
  "preflight_timestamp": 1770391133.159,
  "status": "open",
  "project_path": "/home/user/my-project"
}
```

**Purpose:** Tracks open epistemic transaction. Survives memory compaction.

---

## Resolution Priority Chain

All components use `get_active_project_path()` — the single canonical function.

### TMUX (truly instance-unique)
```
Priority 0: instance_projects/tmux_N.json        (TMUX_PANE → unique per pane)
    ↓
Priority 1: active_work_{claude_session_id}.json  (fallback)
    ↓
❌ NO CWD FALLBACK - return None, fail explicitly
```

### Non-TMUX (X11, macOS Terminal, TTY)
```
Priority 0: active_work_{claude_session_id}.json  (unique per Claude session)
    ↓
Priority 1: instance_projects/{instance_id}.json  (fallback for CLI without session_id)
    ↓
❌ NO CWD FALLBACK - return None, fail explicitly
```

**Why the split:** `TMUX_PANE` is unique per pane — each Claude Code instance gets
its own ID. But `WINDOWID` (X11) and `TERM_SESSION_ID` (macOS) are shared across
all processes in the same terminal emulator. Multiple Claude Code instances in the
same window would overwrite each other's `instance_projects` file.

`active_work_{claude_session_id}` is always unique per Claude Code conversation
(the session_id comes from Claude Code via hook stdin). It's the safe default
for non-tmux environments.

**Why instance_projects first in tmux:** It's the only file writable by BOTH hooks
AND project-switch CLI. After `project-switch`, instance_projects reflects user
intent immediately. `active_work` may be stale (only hooks can update it, and no
hook fires between project-switch and the next tool use).

**Why no self-heal:** If the two files disagree after project-switch, that's
expected and correct — instance_projects has the newer data. No file should
overwrite another. The disagreement resolves naturally when the next SessionStart
hook fires and writes both files consistently.

**Instance ID sources:** `get_instance_id()` resolves from (in priority order):
1. `EMPIRICA_INSTANCE_ID` env var (explicit override)
2. `TMUX_PANE` → `tmux_N` (tmux panes — **only truly instance-unique ID**)
3. `TERM_SESSION_ID` → `term_XXXX` (macOS Terminal.app — shared per window)
4. `WINDOWID` → `x11_N` (X11 windows — shared per terminal emulator)
5. TTY device → `term_pts_N` (fallback, persists in same terminal)
6. `None` (no isolation — legacy behavior)

All formats use underscores for filesystem safety. Instance IDs from sources
3-5 are used for **transaction files** (per-project, no conflict) but NOT
trusted as the primary key for `instance_projects` when `claude_session_id`
is available.

---

## Ownership Model

| Component | Writes | Reads | Has claude_session_id? |
|-----------|--------|-------|------------------------|
| **Hooks** (session-init, post-compact) | `active_work`, `instance_projects`, `tty_sessions` | All | Yes (stdin) |
| **CLI** (project-switch) | `instance_projects`, `tty_sessions` | All | **No** |
| **CLI** (session-create) | `tty_sessions` | All | **No** |
| **PREFLIGHT** | `active_transaction` | All | No |
| **Statusline** | Nothing | All | No |
| **Sentinel** | Nothing | All | Yes (stdin) |

**The asymmetry that matters:** Hooks have `claude_session_id` (from stdin).
CLI commands do not. This means:

- `active_work_{claude_session_id}.json` → **only hooks can write** (need the key)
- `instance_projects/tmux_N.json` → **hooks AND project-switch can write** (keyed by TMUX_PANE)
- `tty_sessions/pts-N.json` → **hooks AND CLI can write** (keyed by TTY device)

**Therefore instance_projects is the most complete source** — it's updated by every
writer in the system. active_work can go stale after project-switch because the CLI
can't update it.

**NEVER self-heal between files.** If `active_work` and `instance_projects` disagree,
instance_projects is more current (it was updated by project-switch). Overwriting
instance_projects from active_work reverses the user's project-switch — this was
bug #11.14.

---

## Data Flow Diagram

```
WRITERS                                    READERS
───────                                    ───────

SessionStart Hook ──────┐                  Sentinel Hook
  Has: claude_session_id│                    │ Reads instance_projects (P0)
  Has: TMUX_PANE        │                    │ Falls back to active_work (P1)
  Writes:               │                    │ Resolves → project_path
  • active_work         │                    │
  • instance_projects   │                  Statusline
  • tty_sessions        │                    │ Same priority chain
                        ▼                    │
              ┌──────────────────┐         CLI Commands
              │ Isolation Files  │           │ Same priority chain
              │                  │◄──────────┤
              │ instance_projects│           │
              │ active_work      │         MCP Server
              │ tty_sessions     │           │ Same priority chain
              └──────────────────┘
                        ▲
project-switch CLI ─────┘
  Has: TMUX_PANE, TTY
  Missing: claude_session_id
  Writes:
  • instance_projects  ← signals project change
  • tty_sessions       ← signals project change
  • CANNOT write active_work (no key)
```

**After project-switch:** `instance_projects` has the new project, `active_work` has
the old project. This is expected. `instance_projects` is read first, so the correct
project is used. `active_work` gets updated when the next SessionStart hook fires.

---

## Key Functions

| Function | Location | Purpose |
|----------|----------|---------|
| `get_active_project_path()` | session_resolver.py | **CANONICAL** - project resolution |
| `get_instance_id()` | session_resolver.py | Get instance ID (tmux_N, x11_N, term_N, or None) |
| `get_tty_key()` | session_resolver.py | Get TTY device name |
| `read_active_transaction()` | session_resolver.py | Read transaction file |
| `write_tty_session()` | session_resolver.py | Write TTY session file |

---

## Design Decisions

### No File-Based Statusline Caching

**Decision (2026-02-06):** Removed file-based statusline caching.

**Rationale:**
- Statusline only refreshes on PREFLIGHT/CHECK/POSTFLIGHT (not every second)
- DB queries are fast (local SQLite)
- Cache caused stale data bugs when projects changed
- Single source of truth (DB) is simpler and more reliable

**What remains:**
- TTY session files (not cache — actual session linkage)
- Transaction files (not cache — transaction state)
- Instance project files (not cache — pane-to-project mapping)

---

## Related Documentation

- [CLAUDE_CODE.md](./CLAUDE_CODE.md) - Claude Code specific patterns (includes compaction flow)
- [MCP_AND_CLI.md](./MCP_AND_CLI.md) - MCP/CLI integration patterns
- [KNOWN_ISSUES.md](./KNOWN_ISSUES.md) - Bug history and debugging
