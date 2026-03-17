# Instance Isolation Architecture

Core concepts and file taxonomy for multi-instance isolation.

## The One Rule

> **Hooks write. Everything else reads.**

Hooks have full context (`claude_session_id` from stdin + `instance_id` from env).
CLI commands, Sentinel, statusline, and MCP are **readers** — they resolve project
context from files that hooks wrote.

The sole exception is **`project-switch`**, which writes to `instance_projects` and
`tty_sessions` as a **signal** to hooks. It cannot write `active_work` because it
doesn't know `claude_session_id`. This is fine — `instance_projects` is read first.

`session-create` also updates `instance_projects` with the new `empirica_session_id`
so that statusline and Sentinel can find the session without waiting for a hook to fire.

## File Taxonomy

### 1. Instance Projects — PRIMARY

**Location:** `~/.empirica/instance_projects/{instance_id}.json`
**Key:** Canonical `get_instance_id()` — `tmux_N` (tmux), `x11:N` (X11), `term_pts_N` (TTY)
**Written by:** Hooks (session-init, post-compact), `project-switch` CLI, AND `session-create` CLI
**Read by:** Everyone (hooks, CLI, statusline, Sentinel)

```json
{
  "project_path": "/home/user/my-project",
  "claude_session_id": "fad66571-1bde-4ee1-aa0d-e9d3dfd8e833",
  "empirica_session_id": "2bc1da78-2a28-4745-b75b-f021d563d819",
  "timestamp": "2026-02-13T01:00:00"
}
```

**Purpose:** Links instance → project. **Most current source** because it's the only
file writable by hooks, project-switch, AND session-create. Works across all environments
(tmux, X11, TTY) via the canonical `get_instance_id()` function.

### 2. Active Work Files (Claude Code) — FALLBACK

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

**Purpose:** Links Claude conversation → project. Fallback for non-TMUX environments
where `instance_id` is unavailable. **Cannot be updated by project-switch** (CLI
doesn't know claude_session_id), so may be stale after a project-switch.

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

```
Priority 0: instance_projects/{instance_id}.json  (get_instance_id() → tmux_N, x11:N, term_pts_N)
    ↓
Priority 1: active_work_{claude_session_id}.json   (fallback when instance_id is None)
    ↓
❌ NO CWD FALLBACK - return None, fail explicitly
```

**Why instance_projects first:** It's writable by hooks, project-switch CLI, AND
session-create CLI. After `project-switch`, instance_projects reflects user intent
immediately. `active_work` may be stale (only hooks can update it, and no hook
fires between project-switch and the next tool use).

**Why no self-heal:** If the two files disagree after project-switch, that's
expected and correct — instance_projects has the newer data. No file should
overwrite another. The disagreement resolves naturally when the next SessionStart
hook fires and writes both files consistently.

**Fallback to active_work:** Only when `get_instance_id()` returns `None` (no tmux,
no X11, no TTY — rare). In most environments, instance_projects handles isolation.

---

## Multi-Instance Without tmux

The isolation mechanism depends on each Claude Code instance getting a unique
`instance_id`. How this works varies by terminal setup:

| Setup | Isolation | Why |
|-------|-----------|-----|
| **tmux panes** | ✅ Full | Each pane has unique `TMUX_PANE` → unique `instance_projects/tmux_N.json` |
| **Separate terminal windows** | ✅ Full | Each window has unique `WINDOWID` → unique `instance_projects/x11_N.json` |
| **Tabs in same terminal** | ⚠️ Shared | Tabs share the same `WINDOWID` → same `instance_projects` file → last writer wins |
| **Single terminal** | ✅ N/A | Only one instance, no conflict |

### Tabs in Same Terminal (Known Limitation)

Multiple Claude Code instances running in **tabs of the same terminal emulator**
(e.g., GNOME Terminal tabs, iTerm2 tabs) share the same X11 `WINDOWID`. This
means `project-switch` in one tab overwrites the `instance_projects` file for
all tabs in that window. The `active_work.json` (generic, no session suffix)
is also shared.

**Symptoms:**
- Statusline shows wrong project after switching in another tab
- Sentinel may gate based on wrong project's transaction state
- `project-bootstrap` loads context from wrong project

**Workarounds:**
1. **Use separate terminal windows** (not tabs) — each gets its own `WINDOWID`
2. **Use tmux** — each pane gets its own `TMUX_PANE`, purpose-built for this
3. **Single project per terminal window** — avoid `project-switch` across tabs

**Why not fix this?** Tabs in the same terminal share the same X11 window ID at
the OS level. There is no environment variable or file descriptor that uniquely
identifies a tab. The `claude_session_id` (from Claude Code's stdin) IS unique
per instance, but CLI commands (including `project-switch`) don't have access to
it — only hooks do. This is a fundamental platform limitation.

---

## Ownership Model

| Component | Writes | Reads | Has claude_session_id? |
|-----------|--------|-------|------------------------|
| **Hooks** (session-init, post-compact) | `active_work`, `instance_projects`, `tty_sessions` | All | Yes (stdin) |
| **CLI** (project-switch) | `instance_projects`, `tty_sessions` | All | **No** |
| **CLI** (session-create) | `instance_projects`†, `tty_sessions` | All | **No** |
| **PREFLIGHT** | `active_transaction` | All | No |
| **Statusline** | Nothing | All | No |
| **Sentinel** | Nothing | All | Yes (stdin) |

†`session-create` only updates `empirica_session_id` in the existing file — it does
not set `project_path` or `claude_session_id` (those are set by hooks and project-switch).

**The asymmetry that matters:** Hooks have `claude_session_id` (from stdin).
CLI commands do not. This means:

- `active_work_{claude_session_id}.json` → **only hooks can write** (need the key)
- `instance_projects/{instance_id}.json` → **hooks AND project-switch set project context** (keyed by `get_instance_id()`); session-create updates session_id only
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
  Has: instance_id      │                    │ Falls back to active_work (P1)
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
project-switch CLI ─────┤
  Has: instance_id, TTY │
  Missing: claude_session_id
  Writes:               │
  • instance_projects   │  ← signals project change
  • tty_sessions        │
  • CANNOT write active_work (no key)
                        │
session-create CLI ─────┘
  Has: instance_id, TTY
  Missing: claude_session_id
  Updates:
  • instance_projects   ← updates empirica_session_id only
  • tty_sessions
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
| `get_instance_id()` | session_resolver.py | Get instance ID (tmux_N, x11, term_pts-N, or None) |
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
