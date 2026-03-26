# Cross-Project Intelligence (1.7.0)

## Overview

Empirica 1.7.0 adds cross-project capabilities: searching knowledge across
all projects and writing artifacts to other projects without context-switching.

## Cross-Project Search

### CLI Usage

```bash
# Search current project + all other projects
empirica project-search --project-id empirica --task "sentinel bypass" --global

# Output includes new section:
# 🔗 Cross-project (other projects' knowledge):
#   1. [memory] Gap in Sentinel gate model... (proj: a76ef65b, score: 0.658)
```

### How It Works

`--global` triggers two searches:
1. **Global learnings** — `global_learnings` collection (high-impact findings synced across projects)
2. **Cross-project scan** — Iterates ALL `project_{id}_{collection}` collections in Qdrant

The cross-project scan:
- Discovers project IDs from Qdrant collection names
- Searches `memory`, `eidetic`, and `episodic` collections per project
- Excludes the current project (avoids duplication)
- Merges results by score, tags with source `project_id`

### API

```python
from empirica.core.qdrant.global_sync import search_cross_project

results = search_cross_project(
    query_text="sentinel bypass detection",
    exclude_project_id="748a81a2-...",  # current project
    collections_to_search=["memory", "eidetic", "episodic"],
    limit=5,
    min_points=1,  # skip empty collections
)
# Returns: List[Dict] with score, project_id, collection_type, text/content/narrative
```

## Cross-Project Artifact Writing

### CLI Usage

```bash
# Write a finding to another project by name
empirica finding-log --project-id empirica-cortex --finding "Ingestor handles 91+ formats" --impact 0.6

# Write an unknown to another project
empirica unknown-log --project-id empirica-workspace --unknown "Does EKG support project entities?"
```

### How It Works

When `--project-id` is a project **name** (not UUID):
1. `_resolve_db_for_artifact()` detects it's not a UUID
2. `_get_db_for_project()` queries `workspace.db` → `global_projects.trajectory_path`
3. Opens `{trajectory_path}/.empirica/sessions/sessions.db`
4. Artifact is written to the TARGET project's database

Falls back to local DB if resolution fails.

### Supported Commands

Currently enabled on:
- `finding-log`
- `unknown-log`

Other artifact commands (`deadend-log`, `assumption-log`, `decision-log`) support
`--project-id` as a UUID but don't yet resolve names to cross-project DBs.
Follow the same pattern in `artifact_log_commands.py` to add.

## Architecture

```
User: empirica finding-log --project-id empirica-cortex --finding "..."
         │
         ▼
_resolve_db_for_artifact("empirica-cortex")
         │
         ├─ _is_uuid("empirica-cortex") → False
         │
         ├─ _get_db_for_project("empirica-cortex")
         │     │
         │     ├─ workspace.db: SELECT trajectory_path FROM global_projects WHERE name = ?
         │     │
         │     └─ Returns: SessionDatabase("/path/to/empirica-cortex/.empirica/sessions/sessions.db")
         │
         └─ db.log_finding(...)  →  Written to empirica-cortex's DB
```
