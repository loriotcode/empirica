# Context Budget API

**Module:** `empirica.core.context_budget`
**Category:** Context Window Management
**Stability:** Production Ready

---

## Overview

The Context Budget Manager treats the AI context window as RAM with paging. It manages allocation, eviction, and injection of context items within the finite token budget.

Three memory zones (like Linux memory zones):

| Zone | Purpose | Size | Evictable |
|------|---------|------|-----------|
| **ANCHOR** | Always-resident (CLAUDE.md, calibration, session IDs) | ~15k tokens | No |
| **WORKING** | Active task context (goals, findings, code) | ~150k tokens | By priority |
| **CACHE** | Preloaded but evictable (protocols, historical findings) | ~35k tokens | First |

---

## ContextBudgetManager

### Constructor

```python
from empirica.core.context_budget import get_budget_manager

manager = get_budget_manager(session_id="abc-123")
```

### Key Methods

| Method | Returns | Description |
|--------|---------|-------------|
| `register_item(item)` | None | Register a context item with zone and priority |
| `request_injection(item_id, reason)` | bool | Request item injection into context |
| `get_budget_report()` | `BudgetReport` | Snapshot of current budget state |
| `evict_stale_items()` | int | Evict expired/low-priority items, return count |

---

## BudgetReport

Snapshot of the current context-window budget state (like `/proc/meminfo`).

### Fields

| Field | Type | Description |
|-------|------|-------------|
| `total_budget` | `int` | Total token budget |
| `used_tokens` | `int` | Currently used tokens |
| `available_tokens` | `int` | Remaining tokens |
| `utilization` | `float` | Usage ratio (0.0-1.0) |
| `zone_breakdown` | `Dict[str, int]` | Tokens per zone (anchor/working/cache) |
| `pressure_level` | `str` | "low", "medium", "high", "critical" |

---

## BudgetEventTypes

Event constants published on the EpistemicBus for budget-related notifications.

| Event | Trigger |
|-------|---------|
| `MEMORY_PRESSURE` | Working set exceeds threshold |
| `BUDGET_EXHAUSTED` | No more tokens available |
| `ITEM_EVICTED` | Context item was evicted |
| `INJECTION_REQUESTED` | Item injection was requested |

---

## AttentionStatus

**Module:** `empirica.core.system_dashboard`

Snapshot of the attention budget calculator state.

| Field | Type | Description |
|-------|------|-------------|
| `has_budget` | `bool` | Whether attention budget remains |
| `total` | `int` | Total attention units |
| `remaining` | `int` | Remaining attention units |
| `utilization` | `float` | Usage ratio |
| `strategy` | `str` | Current allocation strategy |

---

## Tunable Thresholds

Set via environment or config (like `sysctl vm.*` parameters):

| Parameter | Default | Description |
|-----------|---------|-------------|
| `context.anchor_reserve` | 15000 | Tokens reserved for anchor zone |
| `context.working_set_target` | 150000 | Target working set size |
| `context.cache_limit` | 35000 | Maximum cache zone size |
| `context.eviction_aggressiveness` | 0.5 | How aggressively to evict (0-1) |
| `context.decay_rate` | 0.1 | Item relevance decay per hour |

---

## EpistemicBus Integration

The manager sits on the EpistemicBus as an observer:

| Event | Response |
|-------|----------|
| `SESSION_STARTED` | Initialize inventory, load anchor zone |
| `CONFIDENCE_DROPPED` | Page fault: retrieve relevant items |
| `POSTFLIGHT_COMPLETE` | Decay stale items, update references |
| `pre_compact` | Triage for eviction before compaction |

---

## Implementation Files

- `empirica/core/context_budget.py` - ContextBudgetManager, BudgetReport, BudgetEventTypes
- `empirica/core/system_dashboard.py` - AttentionStatus

---

**API Stability:** Stable
**Last Updated:** 2026-03-04
