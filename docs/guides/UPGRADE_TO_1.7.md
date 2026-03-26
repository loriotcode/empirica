# Upgrading to Empirica 1.7.0

## Quick Upgrade

```bash
pip install --upgrade empirica
empirica setup-claude-code --force   # Required: plugin renamed
```

The `--force` flag is **required** for 1.7.0 — it migrates the plugin from
`empirica-integration` to `empirica` and clears stale hooks.

## What's New

### Epistemic Governance

Empirica 1.7.0 introduces three governance mechanisms:

1. **Constitution** (`/empirica-constitution`) — A 12-section decision tree
   that routes situations to the right mechanism. Load it when you hit a
   routing decision you're not sure about.

2. **Epistemic Persistence Protocol** (`/epistemic-persistence-protocol`) —
   Calibrated position-holding under pushback. Replaces the Anti-Agreement
   Protocol with evidence-gated position updates.

3. **Lean Core Prompt** (experimental) — 81% reduction in always-loaded
   context. Keeps identity, vectors, transaction discipline. Everything
   else loads via skills on demand. Opt-in for 1.7.0.

### Cross-Project Intelligence

- **Cross-project search**: `empirica project-search --global` now searches
  ALL projects' Qdrant collections, not just global_learnings
- **Cross-project artifact writing**: `empirica finding-log --project-id <name>`
  writes to another project's database without project-switch

### Sentinel Improvements

- Investigate cool-down prevents CHECK gaming (3 noetic tool calls required)
- Error messages now include escape commands
- Remote command classification for SSH/rsync/scp
- CHECK/Sentinel decision split-brain fixed

## Breaking Changes

### Plugin Rename

The plugin directory changed from `~/.claude/plugins/local/empirica-integration/`
to `~/.claude/plugins/local/empirica/`.

**Action required**: Run `empirica setup-claude-code --force` after upgrading.
This automatically removes the old directory and installs to the new path.

Agent names changed: `empirica-integration:security` → `empirica:security` (etc.)

### Qdrant Deduplication

Previous versions created duplicate Qdrant entries on every `project-embed`
or `rebuild --qdrant` run. 1.7.0 fixes the ID scheme. To clean existing
duplicates:

```bash
empirica rebuild --qdrant
```

This recreates all collections with correct IDs.

## For Plugin Developers

If you reference Empirica agent names in your tools or hooks, update:
- `empirica-integration:*` → `empirica:*`

The `setup-claude-code` API is unchanged — just the output path moved.

## Lean Core Prompt (Experimental)

To try the lean core system prompt:

```bash
# Back up current
cp ~/.claude/empirica-system-prompt.md ~/.claude/empirica-system-prompt.backup.md

# Install lean core
cp $(python3 -c "import empirica; import os; print(os.path.dirname(empirica.__file__))")/plugins/claude-code-integration/templates/empirica-system-prompt-lean.md \
   ~/.claude/empirica-system-prompt.md

# To revert
cp ~/.claude/empirica-system-prompt.backup.md ~/.claude/empirica-system-prompt.md
```

The lean core loads the Constitution skill on demand. Tested across 5 scenarios
(routing, pushback, context management, escalation, natural interpretation).
