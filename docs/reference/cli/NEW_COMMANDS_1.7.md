# Empirica CLI - New Commands Reference (1.7)

Reference documentation for 6 CLI commands introduced in the 1.7 release cycle.

---

### `empirica calibration-dispute`

**Description:** File a dispute when grounded calibration reports a gap that is a measurement bug, not a real overestimate. For example, a greenfield project receiving `change=0.2` when creating an entire repo from scratch. Disputes are stored in SQLite and flagged in subsequent calibration reports.

**Syntax:** `empirica calibration-dispute [flags]`

**Flags:**

| Flag | Required | Default | Description |
|------|----------|---------|-------------|
| `--vector VECTOR` | Yes | -- | Vector name to dispute (e.g., `change`, `impact`, `do`) |
| `--reported REPORTED` | Yes | -- | The grounded value reported by post-test (e.g., `0.2`) |
| `--expected EXPECTED` | Yes | -- | The value you believe is correct (e.g., `0.85`) |
| `--reason REASON` | Yes | -- | Why this measurement is wrong |
| `--evidence EVIDENCE` | No | -- | Supporting evidence (e.g., `"git log --stat shows 8 files created"`) |
| `--session-id SESSION_ID` | No | Active session | Session to dispute |
| `--output {human,json}` | No | `json` | Output format |

**Example:**

```bash
empirica calibration-dispute \
  --vector change \
  --reported 0.2 \
  --expected 0.85 \
  --reason "Greenfield repo, normalization inappropriate" \
  --evidence "git log --stat shows 8 files created from scratch"
```

---

### `empirica engagement-focus`

**Description:** Set or clear the active engagement context. When an engagement is focused, subsequent commands operate within that engagement scope.

**Syntax:** `empirica engagement-focus [engagement_id] [flags]`

**Flags:**

| Flag | Required | Default | Description |
|------|----------|---------|-------------|
| `engagement_id` | No (positional) | -- | Engagement UUID or name to focus |
| `--clear` | No | `false` | Clear the active engagement |
| `--output {json,default}` | No | `default` | Output format |

**Example:**

```bash
# Focus on an engagement by name
empirica engagement-focus acme-onboarding

# Focus on an engagement by UUID
empirica engagement-focus 3fa85f64-5717-4562-b3fc-2c963f66afa6

# Clear the active engagement
empirica engagement-focus --clear
```

---

### `empirica profile-prune`

**Description:** Remove artifacts from SQLite and Qdrant that match pruning rules. Every prune operation is recorded as an immutable receipt in git notes for auditability.

**Syntax:** `empirica profile-prune [flags]`

**Flags:**

| Flag | Required | Default | Description |
|------|----------|---------|-------------|
| `--rule RULE` | No | -- | Apply a specific mechanical pruning rule. Values: `stale-resolved-unknowns`, `test-transactions`, `low-impact-findings`, `falsified-assumptions`, `old-dead-ends`, `low-confidence-imports` |
| `--artifact-id ARTIFACT_ID` | No | -- | Prune a specific artifact by UUID |
| `--artifact-type TYPE` | No | -- | Type of artifact to prune (required with `--artifact-id`). Values: `finding`, `unknown`, `dead_end`, `mistake`, `goal` |
| `--reason REASON` | No | -- | Reason for pruning (recorded in the prune receipt) |
| `--older-than DAYS` | No | -- | Only prune artifacts older than N days |
| `--dry-run` | No | `false` | Show what would be pruned without actually removing anything |
| `--output {json,text}` | No | `json` | Output format |

**Example:**

```bash
# Dry-run: see what stale resolved unknowns would be pruned
empirica profile-prune --rule stale-resolved-unknowns --dry-run

# Prune low-impact findings older than 30 days
empirica profile-prune --rule low-impact-findings --older-than 30

# Prune a specific artifact by UUID
empirica profile-prune --artifact-id 9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d \
  --artifact-type finding \
  --reason "Superseded by later finding"
```

---

### `empirica profile-status`

**Description:** Unified view of the epistemic profile. Shows artifact counts by type, sync state (local vs remote), last sync time, drift between git notes and SQLite, and a calibration summary.

**Syntax:** `empirica profile-status [flags]`

**Flags:**

| Flag | Required | Default | Description |
|------|----------|---------|-------------|
| `--remote REMOTE` | No | From sync config | Git remote to check sync state against |
| `--output {json,text}` | No | `json` | Output format |

**Example:**

```bash
# Show profile status with default remote
empirica profile-status

# Show profile status checking against a specific remote
empirica profile-status --remote forgejo

# Human-readable text output
empirica profile-status --output text
```

---

### `empirica profile-sync`

**Description:** Full profile sync pipeline. Fetches git notes from a remote, imports artifacts idempotently into SQLite (preserving original UUIDs), and optionally rebuilds the Qdrant semantic index.

**Syntax:** `empirica profile-sync [flags]`

**Flags:**

| Flag | Required | Default | Description |
|------|----------|---------|-------------|
| `--remote REMOTE` | No | From sync config (typically `"forgejo"`) | Git remote to sync with |
| `--push` | No | `false` | Push local notes to remote after import (bidirectional sync) |
| `--qdrant` | No | `false` | Rebuild Qdrant semantic index after import |
| `--import-only` | No | `false` | Skip fetch, only import existing local git notes into SQLite |
| `--force` | No | `false` | Force sync even if disabled in config |
| `--output {json,text}` | No | `json` | Output format |

**Example:**

```bash
# Pull notes from remote and import into SQLite
empirica profile-sync

# Full bidirectional sync with Qdrant rebuild
empirica profile-sync --push --qdrant

# Import only from existing local git notes (no network)
empirica profile-sync --import-only

# Force sync against a specific remote
empirica profile-sync --remote origin --force
```

---

### `empirica workspace-search`

**Description:** Search across the workspace, supporting both entity-based filtering and semantic search queries. Results can be scoped to a specific project.

**Syntax:** `empirica workspace-search [flags]`

**Flags:**

| Flag | Required | Default | Description |
|------|----------|---------|-------------|
| `--entity ENTITY` | No | -- | Entity filter in `TYPE/ID` format (e.g., `contact/david`, `org/acme`) |
| `--task TASK` | No | -- | Semantic search query |
| `--project-id PROJECT_ID` | No | -- | Restrict results to a specific project |
| `--limit LIMIT` | No | -- | Maximum number of results to return |
| `--output {json,human}` | No | -- | Output format |

**Example:**

```bash
# Semantic search across the entire workspace
empirica workspace-search --task "authentication flow" --limit 10

# Filter by entity type and ID
empirica workspace-search --entity contact/david

# Combine entity filter with semantic search, scoped to a project
empirica workspace-search --entity org/acme --task "onboarding" \
  --project-id proj-abc123 --output human
```
