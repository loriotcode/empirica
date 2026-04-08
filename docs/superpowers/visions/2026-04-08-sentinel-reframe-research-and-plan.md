# Sentinel Reframe — Research Findings + Implementation Plan

**Date:** 2026-04-08
**Status:** Research + planning (post-vision, pre-spec)
**Authors:** David (with Claude Opus 4.6)
**Related:** `2026-04-08-sentinel-as-compliance-loop.md` (the architectural vision this implements)
**Method:** 3 parallel Explore agents mapping the current state, plus synthesis here.

---

## TL;DR

The Sentinel-as-Compliance-Loop reframe touches **~10,700 LOC across 10 files** and adds **3 new modules**. It can be decomposed into **9 sub-specs** with clear dependencies, of which **3 are independent and can be done in parallel** by separate AI workers. With wave-based parallelization the full reframe is realistically a **4-6 week project**, vs ~7-11 weeks single-threaded. The current architecture has good extension points for the change — minimal coupling refactor is needed for Phase 1, and the biggest single coupling risk is the `_run_single_phase_verification` orchestration hub which already received its first surgery in today's shipped work.

---

## Part 1: Current State Map

### Surface area

| File | LOC | Role |
|---|---:|---|
| `empirica/plugins/claude-code-integration/hooks/sentinel-gate.py` | 2257 | PreToolUse hook: noetic firewall, CHECK validation, dynamic threshold gate, work_type-aware command expansion |
| `empirica/cli/command_handlers/workflow_commands.py` | 2920 | PREFLIGHT/CHECK/POSTFLIGHT command handlers, JSON shapes, post-storage pipeline orchestration |
| `empirica/core/post_test/collector.py` | 2000 | EvidenceItem/Bundle dataclasses, EvidenceProfile enum, 11 universal collectors + prose/web profiles |
| `empirica/core/post_test/grounded_calibration.py` | 1100 | _run_single_phase_verification orchestration, GroundedCalibrationManager (Bayesian belief updates), storage operations, run_grounded_verification wrapper |
| `empirica/core/post_test/dynamic_thresholds.py` | 525 | Brier decomposition (Murphy 1973), reliability-based threshold inflation |
| `empirica/core/post_test/mapper.py` | 542 | EvidenceMapper, WORK_TYPE_RELEVANCE table, GroundedAssessment dataclass, weighted calibration scoring |
| `empirica/config/threshold_loader.py` | 477 | ThresholdLoader singleton, profile selection, create_custom_profile (dynamic injection point) |
| `empirica/cli/validation.py` | 341 | Pydantic input models (PreflightInput, CheckInput, PostflightInput) |
| `empirica/core/post_test/trajectory_tracker.py` | 300 | record_trajectory_point, detect_calibration_trend (linear regression on absolute gaps) |
| `empirica/data/schema/verification_schema.py` | 138 | SQLite table schemas: grounded_beliefs, verification_evidence, grounded_verifications, calibration_trajectory |
| **TOTAL** | **~10,700** | The current Sentinel + calibration + CLI surface |

Plus configuration:
- `empirica/config/mco/cascade_styles.yaml` (~150 lines): threshold profiles
- `.empirica/project.yaml` (per project): evidence_profile setting
- `.breadcrumbs.yaml` (per project): exported calibration state

### Key data structures

**`GroundedAssessment` dataclass** (`mapper.py:170`) — currently 8 fields, the natural place to extend with three-vector storage:
```python
@dataclass
class GroundedAssessment:
    session_id: str
    self_assessed: dict[str, float]
    grounded: dict[str, GroundedVectorEstimate]
    calibration_gaps: dict[str, float]
    grounded_coverage: float
    overall_calibration_score: float
    phase: str = "combined"
    insufficient_evidence_vectors: list[str] = None
    calibration_status: str = "grounded"  # added today
```

**`WORK_TYPE_RELEVANCE` table** (`mapper.py:78-189`) — 12 work_types with per-source relevance multipliers. The natural place to extend with `(work_type, domain, criticality)` tuple keys.

**`EvidenceBundle` dataclass** (`collector.py:62`) — 7 fields after today's additions. Already has `sources_empty` and `source_errors` for fail-loud handling.

**`PreflightInput` Pydantic model** (`validation.py:60`) — currently 6 fields. Natural place to add `domain`, `criticality`, `grounded_rationale`, `predicted_check_outcomes`.

### Storage tables

| Table | Purpose | New columns needed for reframe |
|---|---|---|
| `grounded_beliefs` | Per-AI Bayesian posterior per vector | `state_type` enum (`observed`/`grounded`/`synthesized`) |
| `verification_evidence` | Raw evidence items from collectors | (no change — already raw) |
| `grounded_verifications` | Per-transaction calibration record | `observed_vectors` (JSON), `grounded_rationale` (text), `domain` (already exists), `criticality` (new), `compliance_status` (enum) |
| `calibration_trajectory` | Per-vector drift tracking over time | `state_type` (filter trends by state), `predicted_outcome` / `actual_outcome` for new Brier |
| (NEW) `compliance_checks` | Per-transaction check results | `transaction_id`, `check_id`, `tool`, `criterion`, `result`, `passed`, `failure_detail` |
| (NEW) `domain_registry` | Domain-criticality → checklist mapping | Or YAML files in `~/.empirica/domains/` |

### Key extension points (12 identified)

| # | Where | Current state | Extension |
|---|---|---|---|
| 1 | `WORK_TYPE_RELEVANCE` (mapper.py) | flat dict keyed by work_type | extend to nested dict keyed by (work_type, domain, criticality) tuple |
| 2 | `ThresholdLoader.create_custom_profile` (threshold_loader.py:335) | already supports dynamic profile injection | use it to load domain-specific profiles at PREFLIGHT |
| 3 | `PreflightInput` (validation.py:60) | accepts work_type | add `domain`, `criticality`, `grounded_rationale`, `predicted_outcomes` |
| 4 | `GroundedAssessment` (mapper.py:170) | 2 vector sets | add `observed_vectors` + `grounded_rationale` |
| 5 | `_run_single_phase_verification` (grounded_calibration.py:646) | orchestration hub | refactor to compute observations, present to AI, accept reasoned grounded state |
| 6 | `update_grounded_beliefs` (grounded_calibration.py:217) | Bayesian update on 1 grounded value | extend to 3-state with arbitration logic |
| 7 | `record_trajectory_point` (trajectory_tracker.py:56) | writes 1 row per vector | add `state_type` column, write 3 rows per vector |
| 8 | `compute_dynamic_thresholds` (dynamic_thresholds.py:191) | Brier on vector divergence | new function: Brier on prediction-of-checks |
| 9 | `select_profile_for_work` (threshold_loader.py:226) | (work_type, work_context) → profile name | extend signature with (domain, criticality) |
| 10 | `_validate_check_record` (sentinel-gate.py:1649) | 10-min minimum, vector thresholds | add domain-criticality-aware override of min duration / required rigor |
| 11 | `_run_postflight_storage_pipeline` (workflow_commands.py:1891) | 8 storage operations | add compliance check execution as op #9, with iterative loop trigger |
| 12 | Hook registration (`setup_claude_code.py:207`) | settings.json declarative | (no change — existing pattern is fine) |

### What works well in the current architecture

1. **Tool classification is clean.** NOETIC_TOOLS / SAFE_BASH_PREFIXES / DANGEROUS_SHELL_OPERATORS are well-separated and extensible. The noetic firewall doesn't need to change.
2. **`ThresholdLoader.create_custom_profile()` is the right pattern.** It already supports per-transaction dynamic threshold injection. The compliance loop just needs to call it with domain-derived thresholds.
3. **`insufficient_evidence_vectors` is load-bearing for honest absence.** This prevents fabricated grounded values for vectors with no source evidence — already aligned with the reframe philosophy.
4. **Phase-aware verification** (`run_grounded_verification` wrapper) already splits noetic/praxic. The compliance loop can plug in at the same level.
5. **Disputed-vector 4x variance scaling** (Bayesian update line 262) shows the codebase already understands "be conservative when self vs grounded disagree" — the new architecture formalizes this.

### What the current architecture gets wrong (the comfortable lie)

1. **`holistic_calibration_score` is computed from misnamed proxies.** `know` is graded by artifact volume, not knowledge state. This is what we're fixing.
2. **Brier-based dynamic thresholds optimize for the wrong target.** The Brier is computed on vector divergence from proxies, not on prediction of falsifiable outcomes.
3. **No domain criticality axis.** Every transaction gets the same rigor regardless of stakes.
4. **No iterative loop.** A failing check at POSTFLIGHT doesn't auto-queue follow-up — the AI has to remember to address it manually.
5. **Single grounded value per vector.** Conflates "what was observed" with "what was true," with no rationale layer.

---

## Part 2: Sub-Spec Decomposition

The vision document lists 9 areas needing design. Here's each one with scope estimate and dependencies:

### A1: Domain-criticality registry schema

**What:** Define how `(work_type, domain, criticality)` tuples map to required check lists. YAML files in `~/.empirica/domains/` (or per-project `.empirica/domains.yaml`) with schema validation. New module `empirica/config/domain_registry.py`.

**Touches:**
- NEW: `empirica/config/domain_registry.py` (~300 LOC)
- NEW: `empirica/config/schema/domain_schema.py` (~100 LOC)
- NEW: `~/.empirica/domains/*.yaml` example files
- NEW: `tests/config/test_domain_registry.py` (~200 LOC)
- MODIFY: `empirica/cli/parsers/config_parsers.py` for `empirica domain-list/show/validate` CLI commands (~50 LOC)

**Estimated scope:** ~650 LOC + tests
**Dependencies:** None — fully independent
**Risk:** Low — additive, no existing code coupling

---

### A2: Service-as-checklist DSL

**What:** Each deterministic service declares which `(work_type, domain)` tuples it applies to and what its pass criterion is. Backwards-compatible with the current collector.py source registry — services keep their existing collection logic but add a `register_check()` declaration. New module `empirica/config/service_registry.py`.

**Touches:**
- NEW: `empirica/config/service_registry.py` (~400 LOC)
- NEW: `empirica/config/schema/service_schema.py` (~100 LOC)
- MODIFY: `empirica/core/post_test/collector.py` — add `register_check()` calls in each collector module (~10 LOC × 16 collectors = ~160 LOC)
- NEW: `empirica/core/post_test/prose_collector.py`, `web_collector.py` — same ~160 LOC pattern
- NEW: `tests/config/test_service_registry.py` (~250 LOC)

**Estimated scope:** ~1100 LOC + tests
**Dependencies:** None — service registration is a parallel concern to collection
**Risk:** Low-medium — additive but touches every collector file

---

### A3: Three-vector storage schema

**What:** Schema migration to add `observed_vectors`, `grounded_rationale`, `state_type` columns. Migration script. Backwards-compatible reads (legacy rows assume `state_type=grounded`).

**Touches:**
- MODIFY: `empirica/data/schema/verification_schema.py` (~80 LOC additions)
- NEW: `empirica/data/migrations/2026XX_three_vector_storage.py` (~150 LOC)
- MODIFY: `empirica/core/post_test/mapper.py` — `GroundedAssessment` dataclass (~30 LOC)
- MODIFY: `empirica/core/post_test/grounded_calibration.py` — `store_verification` writes new columns (~50 LOC)
- MODIFY: `empirica/core/post_test/trajectory_tracker.py` — `record_trajectory_point` writes `state_type` (~30 LOC)
- NEW: `tests/data/test_three_vector_migration.py` (~200 LOC)

**Estimated scope:** ~540 LOC + migration script + tests
**Dependencies:** None — schema-only change. The new columns are NULLable / default-valued for backwards compat.
**Risk:** Medium — schema migration on production data needs careful rollout, but the additive nature limits blast radius

---

### B1: Domain-criticality-aware CHECK gate

**What:** `_validate_check_record` in sentinel-gate.py consults the domain registry (A1) to determine required investigation rigor. Different domains have different minimum noetic durations, required artifact counts, and threshold floors.

**Touches:**
- MODIFY: `empirica/plugins/claude-code-integration/hooks/sentinel-gate.py:1649-1707` (`_validate_check_record`) (~100 LOC)
- MODIFY: `empirica/config/threshold_loader.py:226` (`select_profile_for_work`) — add domain/criticality params (~50 LOC)
- NEW: `tests/plugins/test_sentinel_gate_domain_criticality.py` (~250 LOC)

**Estimated scope:** ~400 LOC + tests
**Dependencies:** A1 (domain registry must exist)
**Risk:** Medium — modifies the hot path of the noetic firewall, regression risk on existing flows

---

### B2: Iterative compliance loop coordinator

**What:** New module that runs domain checklists at POSTFLIGHT, identifies failures, and auto-queues follow-up transactions with the failures as scope. Termination conditions (max iterations, manual override).

**Touches:**
- NEW: `empirica/core/post_test/compliance_loop.py` (~600 LOC)
- MODIFY: `empirica/cli/command_handlers/workflow_commands.py:1891` (`_run_postflight_storage_pipeline`) — call compliance loop as op #9 (~80 LOC)
- MODIFY: `empirica/core/post_test/grounded_calibration.py` — emit `compliance_status` enum (~30 LOC)
- NEW: `tests/core/post_test/test_compliance_loop.py` (~400 LOC)

**Estimated scope:** ~1110 LOC + tests
**Dependencies:** A1 (registry) + A2 (services declare themselves) + the schema changes from A3 for `compliance_status` storage
**Risk:** Medium-high — new orchestration logic, needs careful design of termination conditions to avoid infinite loops

---

### B3: AI-reasoned grounded state CLI flow

**What:** Modify POSTFLIGHT command to present observations to the AI and accept the grounded state + rationale. Probably a multi-step flow rather than a single JSON submit. Or: keep the JSON submit but add `grounded_vectors` and `grounded_rationale` fields the AI fills in based on observations from a CHECK-like step.

**Touches:**
- MODIFY: `empirica/cli/validation.py` — add fields to `PostflightInput` (~30 LOC)
- MODIFY: `empirica/cli/command_handlers/workflow_commands.py` — refactor POSTFLIGHT handler to surface observations (~200 LOC)
- MODIFY: `empirica/core/post_test/grounded_calibration.py` — accept AI-reasoned grounded state instead of computing it (~150 LOC)
- NEW: `tests/cli/test_workflow_grounded_rationale.py` (~250 LOC)

**Estimated scope:** ~630 LOC + tests
**Dependencies:** A3 (three-vector schema must exist before storing the new fields)
**Risk:** Medium — touches the POSTFLIGHT happy path that everything else depends on

---

### B4: Brier-on-prediction-of-checks scoring

**What:** Replace vector-divergence Brier with check-outcome Brier. The AI predicts which checks will pass at PREFLIGHT; the actual pass/fail is recorded; Brier compares prediction to outcome. Clean falsifiable signal.

**Touches:**
- NEW: `empirica/core/post_test/check_brier.py` (~400 LOC)
- MODIFY: `empirica/core/post_test/dynamic_thresholds.py` — switch threshold computation source (~150 LOC)
- MODIFY: `empirica/cli/validation.py` — add `predicted_check_outcomes` field to `PreflightInput` (~20 LOC)
- DEPRECATE: vector-divergence Brier path (gradual, behind feature flag)
- NEW: `tests/core/post_test/test_check_brier.py` (~300 LOC)

**Estimated scope:** ~870 LOC + tests
**Dependencies:** A2 (services declare check outcomes) + A3 (storage for predicted vs actual)
**Risk:** Medium — Brier is consumed by sentinel-gate.py for dynamic thresholds, regression risk on the gating logic

---

### C1: Migration plan + dual-write transition

**What:** Documentation + tooling for running the old and new architectures side-by-side during transition. Feature flags. Rollback procedures. Data migration scripts. Communication plan.

**Touches:**
- NEW: `docs/superpowers/specs/2026XX-sentinel-reframe-migration.md` (documentation)
- NEW: `empirica/config/feature_flags.py` (~150 LOC)
- MODIFY: All B1-B4 sites to gate behind feature flags (~50 LOC each)
- NEW: Rollback test in `tests/integration/test_reframe_rollback.py` (~300 LOC)

**Estimated scope:** ~750 LOC + tests + extensive docs
**Dependencies:** All A and B specs (this is the integration layer)
**Risk:** High — coordinates the transition; failures here affect all users of the calibration system

---

### C2: External scanner hook adapters

**What:** Adapters so external tools (semgrep, trivy, gitleaks, vale, etc.) can plug into the service registry without writing collector code in empirica itself. Generic command-runner adapter + JSON output parser + mapping to compliance check schema.

**Touches:**
- NEW: `empirica/integrations/external_scanners/__init__.py` (~50 LOC)
- NEW: `empirica/integrations/external_scanners/generic_runner.py` (~300 LOC)
- NEW: `empirica/integrations/external_scanners/semgrep.py`, `trivy.py`, `gitleaks.py`, etc. (~100 LOC each, ~6 scanners)
- NEW: `tests/integrations/test_external_scanners.py` (~400 LOC)

**Estimated scope:** ~1350 LOC + tests
**Dependencies:** A2 (service registry must exist)
**Risk:** Low — completely additive, no existing code modified

---

## Part 3: Dependency Graph

```
Wave 1 (independent — can run in parallel):
  ┌──────────────────────────┐
  │ A1: Domain registry      │
  │ A2: Service registry DSL │
  │ A3: Three-vector schema  │
  └──────────────────────────┘
              │
              ▼
Wave 2 (depend only on one Wave 1 item):
  ┌──────────────────────────┐
  │ B1: Domain CHECK gate    │ ← A1
  │ B3: AI-reasoned CLI      │ ← A3
  └──────────────────────────┘
              │
              ▼
Wave 3 (depend on multiple Wave 1 items):
  ┌──────────────────────────┐
  │ B2: Compliance loop      │ ← A1 + A2 + A3
  │ B4: Check-outcome Brier  │ ← A2 + A3
  └──────────────────────────┘
              │
              ▼
Wave 4 (depend on most prior work):
  ┌──────────────────────────┐
  │ C1: Migration plan       │ ← A1 + A2 + A3 + B1 + B2 + B3 + B4
  │ C2: External scanner ad. │ ← A2 (independent of B/C otherwise)
  └──────────────────────────┘
```

C2 actually only needs A2 — it could run in parallel with Wave 2 or 3 if there's bandwidth.

---

## Part 4: Parallelization Plan

### Wave-based schedule

| Wave | Specs | Parallel AIs needed | Calendar time (per AI) | Wall time |
|---|---|---|---|---|
| **Wave 1** | A1, A2, A3 | 3 | ~5 days each | ~5 days |
| **Wave 2** | B1, B3 (+ optionally C2) | 2-3 | ~5-7 days each | ~7 days |
| **Wave 3** | B2, B4 | 2 | ~7-10 days each | ~10 days |
| **Wave 4** | C1 + integration testing | 1 (lead) | ~5-7 days | ~7 days |
| **Total** | 9 specs | up to 3 in parallel | ~22-30 dev-days | **~4-5 weeks wall time** |

Single-threaded equivalent (no parallelization): **~7-11 weeks** (per Agent 1's estimate).

**Speedup: ~1.7×–2.2×** depending on whether C2 runs in Wave 2 or later, and how much synchronization friction there is between AIs.

### Minimum viable team

- **3 AIs in Wave 1** (independent specs, no synchronization)
- **2 AIs in Waves 2-3** (some synchronization needed)
- **1 lead AI in Wave 4** (integration + migration coordination)

The orchestrating human (you) is a constant across all waves: review, decision, course-correction.

### Synchronization model

The biggest risk in parallelization is API drift — three AIs writing independent specs that don't compose well at integration time. Mitigation:

**Before Wave 1 begins**, write a **shared API contract document** (~1 day work) that defines:

1. The shape of `domain_registry` entries (what A1 must produce, what B1/B2 will consume)
2. The shape of service `register_check()` declarations (what A2 must produce, what B2/B4/C2 will consume)
3. The new SQL columns and types for the three-vector schema (what A3 must produce, what B3/B4 will consume)
4. The shape of `compliance_status` enum and its transitions (what B2 will emit, what C1 will consume)
5. The shape of the new POSTFLIGHT JSON request/response with grounded rationale (what B3 must produce, what consumers will see)
6. Backwards-compatibility commitments (what stays unchanged for legacy callers)

The contract document is the "interface" all parallel work writes against. Drift becomes detectable at code review time, not at integration time.

**During each wave**, lightweight sync points:
- Daily 10-minute writeup from each AI about progress and any contract questions
- Human reviews each AI's commits for contract compliance before they merge
- Cross-AI questions surfaced through the human, not direct AI-to-AI (avoids context collisions)

**Between waves**, integration checkpoints:
- All Wave 1 work merged before Wave 2 starts
- Wave 1 merge produces a release candidate for Wave 2 to build on
- Same pattern between Waves 2-3 and 3-4

### What goes to which AI

This is a suggestion — exact assignment depends on AI specialties and availability:

**Wave 1 (parallel):**
- **AI-α**: A1 (Domain registry) — config-heavy, schema design, YAML parsing
- **AI-β**: A2 (Service DSL) — declarative API design, decorator patterns, integration with collectors
- **AI-γ**: A3 (Three-vector schema) — SQL migration, dataclass extension, backwards compat

**Wave 2 (parallel after Wave 1 merge):**
- **AI-α** (continuing): B1 (CHECK gate domain awareness) — natural progression, same touched files
- **AI-β** (continuing): B3 (CLI grounded rationale flow) — progression to CLI surface
- **AI-γ or new AI-δ**: C2 (External scanner adapters) — independent track, no dependencies on B1/B3

**Wave 3 (parallel after Wave 2 merge):**
- **AI-α** (continuing): B2 (Compliance loop coordinator) — biggest single piece, high orchestration
- **AI-β** (continuing): B4 (Check-outcome Brier) — math/scoring, modifies dynamic_thresholds.py

**Wave 4 (sequential, lead AI):**
- **AI-lead** (probably me, or a single dedicated AI): C1 + integration testing + migration docs + rollout coordination

### Risks and mitigations

| Risk | Mitigation |
|---|---|
| Three AIs design APIs that don't compose | Shared contract document upfront, daily sync, human review for contract compliance |
| Wave 1 changes break existing tests during merge | Each AI runs full `tests/core/post_test/` before commit; CI gates merge |
| Schema migration (A3) corrupts existing data | Migration is additive only (NULL columns), with rollback script; tested against snapshot of production DB |
| Compliance loop (B2) creates infinite iteration cycles | Hard cap on iterations (default 5), manual override, idempotency check on auto-queued transactions |
| Brier scoring change (B4) silently breaks dynamic threshold gate (B1's substrate) | Both implementations run side-by-side via feature flag; new is opt-in until validated |
| C1 migration plan is wrong, production users get stuck | Phased rollout: empirica-cortex → empirica → external users; rollback procedure tested |
| AI worker context exhaustion mid-wave | Each spec is sized to fit comfortably in one AI's context (~1500 LOC max per spec); checkpoint commits every step per existing TDD discipline |
| Coordination overhead exceeds parallelization gains | Cap parallel AIs at 3; serialize when synchronization cost > parallelization benefit |

---

## Part 5: Recommended Sequencing

### Option A: Aggressive parallelization (4-5 weeks wall time)

1. **Week 0 (now)**: Write the API contract document (~1 day, lead AI)
2. **Week 1**: Wave 1 (3 AIs in parallel) — A1, A2, A3
3. **Week 2**: Wave 1 merge + integration test; Wave 2 starts (2-3 AIs) — B1, B3, optionally C2
4. **Week 3**: Wave 2 merge; Wave 3 starts (2 AIs) — B2, B4
5. **Week 4**: Wave 3 merge; Wave 4 (1 lead AI) — C1 + integration + migration docs
6. **Week 5**: Buffer for rollback fixes, integration issues, doc polish

### Option B: Conservative serial (7-11 weeks wall time)

1. Single AI works through specs in dependency order: A1 → A2 → A3 → B1 → B3 → B2 → B4 → C2 → C1
2. Each spec gets dedicated focus, no synchronization friction
3. Lower coordination overhead, slower wall-time
4. Lower risk of API drift, higher risk of context exhaustion mid-spec

### Option C: Hybrid (5-7 weeks wall time)

1. Lead AI does Wave 1 in series (A1 → A2 → A3) over ~3 weeks — maximally cohesive
2. Wave 2-4 done by lead AI with occasional parallel dispatch for independent pieces (C2 alongside B1 in Week 4-5, etc.)
3. Lower coordination overhead, moderate parallelization

**My recommendation: Option C (hybrid)** — for the following reasons:

1. **Wave 1 is foundational** — the API contracts get refined as A1/A2/A3 are written. Doing them sequentially with the same AI lets the contracts adapt to what's actually needed, instead of being frozen upfront.
2. **The risk of Wave 1 API drift is highest precisely because they're independent**. Sequential mitigates this for free.
3. **Wave 2-4 can parallelize without contract risk** because by then the foundation is set. C2 in particular is fully independent and can run alongside any later wave.
4. **Single-AI continuity preserves context across Waves**. Each subsequent wave benefits from the full mental model built up in earlier waves. Three AIs starting fresh in Wave 1 lose this.
5. **Coordination overhead is real**. Three parallel AIs means three sets of code reviews, three sync conversations, three context loads from you. Hybrid keeps you in the loop but doesn't tax your bandwidth.

**Tradeoff**: Option C gives up some wall-time savings. Wall time goes from ~4-5 weeks (Option A) to ~5-7 weeks (Option C). The difference (~1-2 weeks) is the cost of the lower coordination risk.

If you want to push for maximum speed (Option A), the API contract document becomes load-bearing. If we get that right upfront, parallel Wave 1 works. If we don't, Wave 1 ships specs that don't compose and Wave 2 stalls.

---

## Part 6: What Happens Next

If you approve the plan:

1. **Right now** (today, after Frederike): I write the API contract document as a separate spec. ~1 day estimated. This is the highest-leverage piece because it determines whether parallelization is safe.

2. **Before next week**: We decide between Options A, B, C. Default recommendation: C (hybrid).

3. **Wave 1 starts next week**: Either I (Option C) or 3 parallel AIs (Option A) begin work on A1/A2/A3.

4. **Each spec follows the same TDD discipline as the work shipped today**: failing test → implementation → run → commit. Empirica transactions for measurement. POSTFLIGHTs at every coherent chunk.

5. **The vision document** (`2026-04-08-sentinel-as-compliance-loop.md`) stays as the philosophical anchor. This research+plan document is the operational map. The API contract document (TBD) is the integration spec. Each sub-spec builds on these three.

---

## Part 7: Open Questions

These need decisions before Wave 1 starts:

1. **Domain registry storage location**: per-project `.empirica/domains.yaml`, or user-global `~/.empirica/domains/*.yaml`, or both? (recommendation: both, with per-project taking precedence)

2. **Compliance loop max iterations**: 3? 5? 10? Configurable per domain? (recommendation: 5 default, configurable in registry)

3. **Brier transition strategy**: feature flag with gradual cutover, or hard switch at a specific release? (recommendation: feature flag, dual-write, validate over 2 weeks before deprecating old path)

4. **Backwards compatibility commitment**: which existing CLI shapes / table schemas / file formats get hard-frozen? (recommendation: PREFLIGHT/CHECK/POSTFLIGHT JSON request shapes are frozen for legacy callers; new fields are additive; new CLI commands are net-new)

5. **External user notification**: do we ship the reframe as a major version? Communicate via release notes? (recommendation: 2.0.0 release with detailed migration guide; 1.x branch maintained for 90 days)

6. **AI worker assignment**: do we have multiple AI workers available, or is this all me? (depends on your operational setup — affects Option A vs C)

---

## Provenance

This document was produced by:
1. Reading the vision document `2026-04-08-sentinel-as-compliance-loop.md`
2. Dispatching 3 parallel `Explore` agents to map the current state (sentinel gating, calibration pipeline, CLI surface)
3. Synthesizing the 3 reports into the unified scope + dependency + parallelization plan above

Total research time: ~30 minutes (parallel agent dispatch + synthesis). Each agent returned ~140K tokens of structured findings; the synthesis reduces it to this ~12-page operational map.

The 3 agent reports are not preserved as separate artifacts — they were intermediate context. If you want them captured, I can rerun with `run_in_background=true` and store the outputs.

---

*"Three AIs in parallel can produce a system in 4-5 weeks. One AI in series can produce the same system in 7-11 weeks. The right choice depends on coordination cost, not wall-time savings alone."*
