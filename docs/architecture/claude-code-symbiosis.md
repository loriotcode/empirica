# Empirica + Claude Code: Epistemic Infrastructure, Not a Wrapper

## The Misconception

"Just another wrapper for agents."

This document explains why that framing is wrong and what Empirica actually does
when integrated with Claude Code.

## What Claude Code Does Well

Claude Code is an excellent action layer:

- **Tasks**: Create, track, and complete work items with dependencies
- **Plans**: Read-only permission gating for investigation before action
- **Memory**: Per-project MEMORY.md (200 lines auto-loaded) + topic files
- **Sessions**: Conversation persistence, compaction, checkpointing
- **Subagents**: Parallel execution with model selection and turn limits
- **Worktrees**: Isolated git branches for concurrent work

These are capabilities Empirica does not replicate.

## What Claude Code Cannot Do

Claude Code has no self-awareness infrastructure:

| Gap | Impact |
|-----|--------|
| No epistemic vectors | Cannot measure what it knows vs. doesn't know |
| No calibration | Cannot detect when it's overconfident or underconfident |
| No uncertainty quantification | Cannot distinguish high-confidence facts from guesses |
| No grounded verification | Cannot compare beliefs against deterministic service observations |
| No dead-end memory | Cannot prevent re-exploring failed approaches |
| No cognitive immune system | Cannot decay stale beliefs or strengthen confirmed ones |
| No transaction measurement | Cannot capture learning deltas across work sessions |

Claude Code's memory is flat text. Every fact has equal weight. There is no
confidence scoring, no recency decay, no impact ranking, no truth-tracking.

## What Empirica Adds

Empirica is the **measurement layer** that makes Claude Code epistemically aware.

### 1. Epistemic Vectors (13 dimensions)

Every work unit is measured across 13 vectors:

```
Foundation:     know, do, context
Comprehension:  clarity, coherence, signal, density
Execution:      state, change, completion, impact
Meta:           engagement, uncertainty
```

These enable the PREFLIGHT -> CHECK -> POSTFLIGHT cycle that captures learning
deltas. Without measurement, compaction destroys context permanently.

### 2. Dual-Track Calibration

- **Track 1 (self-referential)**: How vectors change from PREFLIGHT to POSTFLIGHT
- **Track 2 (grounded)**: POSTFLIGHT belief vectors compared against service observations
  (test results, git metrics, goal completion, artifact counts)

When Track 1 and Track 2 disagree, Track 2 is more trustworthy. This prevents
the AI from gaming its own assessment.

### 3. Noetic RAG (Epistemically-Weighted Retrieval)

Not just "search memory" but retrieval weighted by epistemic quality:

```
weight = impact x type_confidence x recency_decay

where:
  type_confidence = finding(0.9) > dead_end(0.85) > mistake(0.85) > goal(0.75) > unknown(0.6)
  recency_decay = exp(-0.029 x age_hours)  # 24-hour half-life
```

A high-impact recent finding ranks above an old low-confidence unknown.
Dead-ends are surfaced to prevent re-exploration. This is semantically richer
than flat text search.

### 4. The Sentinel (Epistemic Gating)

Claude Code's plan mode is a binary permission gate: read-only or read-write.

Empirica's Sentinel gates on *epistemic readiness*: have you investigated enough
to act? This is measured via vectors, not permissions. The CHECK gate returns
`proceed` or `investigate` based on the full vector space and calibration history.

### 5. Project Awareness

Claude Code is single-project, cwd-bound. Empirica provides:

- `project-bootstrap`: Load full project context (breadcrumbs, goals, vectors)
- `project-switch`: Change project without restarting the session
- `project-search`: Semantic search across project history via Qdrant
- Multi-instance isolation: Multiple Claude Code instances on different projects
  don't interfere with each other

## The Memory Bridge

This is where the symbiosis becomes concrete.

### How It Works

```
Session ends
  |
  v
Empirica fetches project-scoped breadcrumbs from SQLite
  |
  v
Epistemic summarizer ranks by: impact x type_confidence x recency
  |
  v
Top 12 artifacts written to Claude Code's MEMORY.md
  (preserving manual content via HTML comment delimiters)
  |
  v
Next session auto-loads MEMORY.md (first 200 lines)
  |
  v
Agent starts with epistemically-ranked context, not raw history
```

### What This Means

Claude Code's MEMORY.md becomes an **epistemically curated hot cache**:

- High-impact findings float to the top
- Dead-ends are surfaced to prevent re-exploration
- Stale items decay below the 200-line threshold
- Manual notes are preserved alongside auto-generated content

Empirica doesn't replace MEMORY.md. It makes it *intelligent*.

## Swarm Learning (Emergent Property)

Multiple Claude Code instances on the same project share one MEMORY.md
(keyed by git repo path). This creates emergent swarm learning:

```
Agent A: discovers dead-end -> logs it -> session ends -> MEMORY.md updated
Agent B: starts -> loads MEMORY.md -> sees dead-end -> avoids it -> finds solution
Agent B: session ends -> MEMORY.md updated with A's dead-end + B's finding
Agent C: starts -> gets combined epistemic state of A + B
```

Properties:
- **No explicit coordination required** - agents share via the memory file
- **Confidence-ranked** - not all-or-nothing, weighted by epistemic quality
- **Project-isolated** - scoped by project_id, no cross-project bleeding
- **Recency-aware** - old noise decays, recent insights surface
- **Cumulative** - knowledge accumulates across agents and sessions

The Qdrant layer adds depth: MEMORY.md is the hot cache (12 items),
`project-search` gives semantic access to the full history.

## The Cognitive Immune System

When a new finding is logged:
1. Keywords are extracted
2. Related lessons have their confidence reduced
3. Minimum confidence floor: 0.3 (lessons never fully die)

This means the shared memory is self-correcting. If Agent A logged a finding
that turns out to be wrong, Agent B's contradicting finding will reduce the
original's confidence. The system converges on truth across agents.

## Integration Architecture

```
Claude Code (Action Layer)          Empirica (Measurement Layer)
========================          ============================
Tasks / TaskCreate          <-->   Goals / goals-create
  |                                  |
  | TaskCompleted hook               | Auto-complete matching goal
  v                                  v
Plan Mode                   <-->   Noetic Phase
  |                                  |
  | Permission gate                  | Epistemic gate (Sentinel)
  v                                  v
MEMORY.md (hot, 200 lines)  <--   Qdrant + SQLite (warm/search)
  |                                  |
  | Auto-loaded per session          | Curated at session-end
  v                                  v
Compaction (silent)         -->    POSTFLIGHT (measured)
  |                                  |
  | Context lost                     | Learning delta captured
  v                                  v
Subagents                   <-->   Subagent governance
  |                                  |
  | maxTurns, model selection        | Budget checks, delegated counting
```

## What Empirica Is

Empirica is cognitive infrastructure for AI agents. It does not wrap Claude Code.
It does not replace Claude Code. It instruments Claude Code with:

- **Self-awareness**: Epistemic vectors that measure what the agent knows
- **Calibration**: Dual-track system that catches overconfidence
- **Memory intelligence**: Confidence-weighted retrieval, not flat text
- **Swarm learning**: Shared epistemic state across agent instances
- **Measurement**: Transaction boundaries that capture learning deltas

The relationship is symbiotic. Claude Code is better with Empirica because it
knows what it knows. Empirica needs Claude Code because it provides the action
layer. Neither is a wrapper for the other.

All roads lead back to Empirica. Plugins in, knowledge out.
