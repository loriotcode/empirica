# Memory & Context Architecture

> How Claude Code + Empirica loads, stores, and retrieves knowledge across sessions.

---

## Four-Tier Architecture

```
+================================================================+
|                    TIER 1: ALWAYS LOADED                        |
|                  (injected every message)                       |
|                                                                |
|  +------------------+  +-------------------+  +--------------+ |
|  | System Prompt    |  | ~/.claude/CLAUDE.md|  | MEMORY.md    | |
|  | (model identity, |  | --> @empirica-     |  | (auto-memory | |
|  |  vectors, rules) |  |    system-prompt   |  |  index, 200  | |
|  +------------------+  +-------------------+  |  lines max)  | |
|                                                +--------------+ |
|  +------------------+  +-------------------+                    |
|  | {project}/       |  | Hook output       |                    |
|  | .claude/CLAUDE.md|  | (SessionStart,    |                    |
|  | (project-local)  |  |  UserPromptSubmit)|                    |
|  +------------------+  +-------------------+                    |
+================================================================+
                             |
                             v
+================================================================+
|                   TIER 2: ON-DEMAND                             |
|            (loaded by hooks/skills when relevant)               |
|                                                                |
|  +------------------+  +-------------------+  +--------------+ |
|  | memory/*.md      |  | .breadcrumbs.yaml |  | Qdrant       | |
|  | (individual      |  | (calibration,     |  | search       | |
|  |  memory files,   |  |  bias corrections,|  | (eidetic,    | |
|  |  loaded on       |  |  loaded at        |  |  episodic,   | |
|  |  relevance)      |  |  session start)   |  |  at PRE/CHK) | |
|  +------------------+  +-------------------+  +--------------+ |
|                                                                |
|  +--------------------------------------------------+          |
|  | project-bootstrap output                          |          |
|  | (findings, goals, unknowns, calibration —         |          |
|  |  loaded at session start and post-compact)        |          |
|  +--------------------------------------------------+          |
+================================================================+
                             |
                             v
+================================================================+
|                TIER 3: PERSISTENT STORAGE                       |
|             (queried on demand, never bulk-loaded)              |
|                                                                |
|  +------------------+  +-------------------+  +--------------+ |
|  | sessions.db      |  | workspace.db      |  | Qdrant       | |
|  | (reflexes, goals,|  | (cross-project    |  | collections  | |
|  |  artifacts,      |  |  registry,        |  | (4 per proj: | |
|  |  calibration,    |  |  entities,        |  |  docs, memory| |
|  |  snapshots)      |  |  contacts)        |  |  eidetic,    | |
|  +------------------+  +-------------------+  |  episodic)   | |
|                                                +--------------+ |
|  +--------------------------------------------------+          |
|  | Git notes --ref=breadcrumbs                       |          |
|  | (portable epistemic snapshots, survives repo      |          |
|  |  clone/transfer)                                  |          |
|  +--------------------------------------------------+          |
+================================================================+
                             |
                             v
+================================================================+
|                TIER 4: TRANSIENT STATE                          |
|             (per-session, ephemeral, auto-cleaned)              |
|                                                                |
|  +------------------+  +-------------------+  +--------------+ |
|  | instance_projects|  | active_work_      |  | active_      | |
|  | /{id}.json       |  | {session}.json    |  | transaction_ | |
|  | (terminal -->    |  | (session -->      |  | {suffix}.json| |
|  |  project bind)   |  |  project bind)    |  | (open tx)    | |
|  +------------------+  +-------------------+  +--------------+ |
|                                                                |
|  +------------------+  +-------------------+  +--------------+ |
|  | hook_counters_   |  | context_usage.json|  | *.jsonl      | |
|  | {suffix}.json    |  | (context % for    |  | (session     | |
|  | (tool call count)|  |  compact advisory)|  |  transcripts)| |
|  +------------------+  +-------------------+  +--------------+ |
+================================================================+
```

---

## Data Flow

```
  Session Start                    Mid-Session                   Session End
  ===========                      ===========                   ===========

  SessionStart hook                PreToolUse hook               SessionEnd hook
       |                                |                              |
       v                                v                              v
  session-init.py               sentinel-gate.py            session-end-postflight.py
       |                                |                              |
       +-- session-create               +-- read transaction           +-- auto POSTFLIGHT
       +-- project-bootstrap            +-- read hook_counters         +-- save to DB
       +-- write instance_projects      +-- classify tool              +-- episodic sync
       +-- write active_work            +-- gate (noetic/praxic)       +-- Cortex push
       +-- Cortex pull                  +-- increment counter          +-- cleanup transient
       |                                |                              |
       v                                v                              v
  Context loaded                   Tool allowed/blocked          State persisted
  into conversation                with reason                   for next session
```

---

## File Locations

| Scope | Path | Purpose |
|-------|------|---------|
| **Global** | `~/.claude/CLAUDE.md` | User instructions (includes Empirica system prompt) |
| **Global** | `~/.claude/settings.json` | Hooks, plugins, statusline configuration |
| **Global** | `~/.empirica/` | Global state (active_work, instance_projects, config) |
| **Global** | `~/.empirica/workspace.db` | Cross-project entity registry |
| **Per-project** | `{project}/.claude/CLAUDE.md` | Project-specific overrides |
| **Per-project** | `{project}/.empirica/sessions/sessions.db` | All epistemic data |
| **Per-project** | `{project}/.empirica/active_transaction_*.json` | Open transaction state |
| **Per-project** | `{project}/.empirica/hook_counters_*.json` | Tool call counts |
| **Per-project** | `{project}/.breadcrumbs.yaml` | Calibration data (auto-updated) |
| **Per-conversation** | `~/.claude/projects/{path}/memory/` | Auto-memories (CC native) |
| **Per-conversation** | `~/.claude/projects/{path}/*.jsonl` | Full transcripts |

---

## Key Design Principles

1. **Tier 1 is token-budgeted** — MEMORY.md capped at 200 lines, system prompt is lean (~1200 tokens)
2. **Tier 2 is hook-injected** — Loaded only when relevant, via SessionStart and PREFLIGHT hooks
3. **Tier 3 is queried** — Never bulk-loaded. CLI commands and Qdrant search retrieve specific data
4. **Tier 4 is ephemeral** — Auto-cleaned by stale file detection and session-end hooks
5. **No CWD fallback (mid-session)** — Project resolution uses instance files, not filesystem. Exception: `startup` events prefer CWD over stale instance files
6. **Git notes are portable** — Epistemic state survives repo clone/transfer via `--ref=breadcrumbs`
7. **Qdrant is optional** — Core works without it. Semantic search and pattern injection require it.
