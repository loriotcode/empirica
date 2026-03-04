# Configuration & Profiles API

**Module:** `empirica.config`
**Category:** Investigation Configuration
**Stability:** Production Ready

---

## Overview

Configuration modules control how the Sentinel gates investigation, how goals are scoped, and how the epistemic workflow adapts to different domains and investigation styles.

---

## InvestigationProfile

**Module:** `empirica.config.profile_loader`

Complete investigation profile aggregating all configuration objects.

### Dataclass Fields

| Field | Type | Description |
|-------|------|-------------|
| `name` | `str` | Profile name (e.g., "default", "deep_investigation") |
| `description` | `str` | Human-readable description |
| `investigation` | `InvestigationConstraints` | Phase constraints |
| `action_thresholds` | `ActionThresholds` | Sentinel gate thresholds |
| `tuning` | `TuningParameters` | Confidence calculation weights |
| `strategy` | `StrategyConfig` | Domain detection and tool selection |
| `learning` | `LearningConfig` | Postflight mode and validation |

### Methods

- `to_dict() -> Dict` - Serialize to dictionary
- `from_yaml(path) -> InvestigationProfile` - Load from YAML config

---

## InvestigationConstraints

**Module:** `empirica.config.profile_loader`

Controls investigation phase limits used by the Sentinel.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `max_rounds` | `Optional[int]` | `None` | Max investigation rounds (None = unlimited) |
| `confidence_threshold` | `float` | `0.65` | Min confidence to proceed |
| `confidence_threshold_dynamic` | `bool` | `False` | Adapt threshold based on history |
| `tool_suggestion_mode` | `ToolSuggestionMode` | `SUGGESTIVE` | How tools are suggested |
| `allow_novel_approaches` | `bool` | `True` | Allow AI to try new approaches |
| `require_tool_approval` | `bool` | `False` | Require approval before tool use |

---

## ActionThresholds

**Module:** `empirica.config.profile_loader`

Thresholds used by the Sentinel to determine proceed vs. investigate.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `uncertainty_high` | `float` | `0.75` | Uncertainty above this triggers investigation |
| `clarity_low` | `float` | `0.45` | Clarity below this triggers investigation |
| `foundation_low` | `float` | `0.45` | Foundation below this triggers investigation |
| `confidence_proceed_min` | `float` | `0.65` | Minimum confidence to proceed |
| `override_allowed` | `bool` | `True` | Allow AI to override thresholds |
| `escalate_on_uncertainty` | `bool` | `False` | Escalate when uncertainty is high |

---

## Enums

### ToolSuggestionMode

| Value | Description |
|-------|-------------|
| `LIGHT` | Minimal suggestions, AI explores freely |
| `SUGGESTIVE` | Suggestions provided, AI decides |
| `GUIDED` | Strong guidance, AI follows |
| `PRESCRIBED` | Specific tools required |
| `INSPIRATIONAL` | Spark ideas for exploration |

### DomainDetection

| Value | Description |
|-------|-------------|
| `REASONING` | AI reasons about domain |
| `PLUGIN_ASSISTED` | Plugins provide hints |
| `HYBRID` | Mix of reasoning + plugins |
| `DECLARED` | User declares domain |
| `EMERGENT` | Discover through exploration |

### PostflightMode

| Value | Description |
|-------|-------------|
| `GENUINE_REASSESSMENT` | AI genuinely reassesses vectors |
| `COMPARATIVE_ASSESSMENT` | Compare pre/post explicitly |
| `FULL_AUDIT_TRAIL` | Complete audit of changes |
| `REFLECTION` | Focus on learning takeaways |

---

## GoalScopeLoader

**Module:** `empirica.config.goal_scope_loader`

Maps epistemic vector patterns to recommended goal scope vectors. Advisory only — AI and Sentinel can override.

### Constructor

```python
loader = GoalScopeLoader(config_path=None)  # Uses default goal_scopes.yaml
```

### Key Methods

- `get_recommendations(vectors: Dict[str, float]) -> Dict` - Get scope recommendations
- `validate_scope(scope: Dict, vectors: Dict) -> Dict` - Check scope coherence

### Example

```python
from empirica.config.goal_scope_loader import get_scope_recommendations

recommendations = get_scope_recommendations(
    epistemic_vectors={"know": 0.85, "uncertainty": 0.3, "clarity": 0.80}
)
# Returns: {"breadth": 0.3, "duration": 0.2, "coordination": 0.1}
```

---

## Implementation Files

- `empirica/config/profile_loader.py` - InvestigationProfile, constraints, thresholds
- `empirica/config/goal_scope_loader.py` - GoalScopeLoader, scope recommendations
- `empirica/config/mco/` - YAML configuration files

---

**API Stability:** Stable
**Last Updated:** 2026-03-04
