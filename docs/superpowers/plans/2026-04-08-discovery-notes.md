# Discovery findings — sentinel-measurer-remote-ops plan

**Date:** 2026-04-08
**Transaction:** T0 (pre-implementation discovery)
**For:** Implementation of `docs/superpowers/plans/2026-04-08-sentinel-measurer-remote-ops.md`

---

## Discovery 1: `previous_transaction_feedback` aggregation

**Location:** `empirica/cli/command_handlers/workflow_commands.py:580-654`

**Mechanism:** SELECT against `grounded_verifications` table, joined with `sessions` for project filter. Returns the most recent row, extracts `calibration_gaps` JSON, computes `overestimate_tendency` and `underestimate_tendency` lists from gaps with `abs(g) > 0.1`.

**Schema query (line 584-592):**
```sql
SELECT gv.calibration_gaps, gv.overall_calibration_score,
       gv.grounded_coverage
FROM grounded_verifications gv
JOIN sessions s ON gv.session_id = s.session_id
WHERE gv.ai_id = ? AND s.project_id = ?
ORDER BY gv.created_at DESC
LIMIT 1
```

**Conclusion:** No code change needed. See Discovery 4 for why.

---

## Discovery 2: `sentinel-gate.py` metacog read site

**Location:** `empirica/plugins/claude-code-integration/hooks/sentinel-gate.py:208-223`

**Mechanism:** `_get_dynamic_thresholds(db)` calls `compute_dynamic_thresholds(ai_id="claude-code", db=db)` from `empirica/core/post_test/dynamic_thresholds.py`. Reads `noetic.brier_score` from the result. Used to compute dynamic `know` and `uncertainty` thresholds for gating.

**Single read site at line 219:**
```python
if noetic.get("brier_score") is not None:
    return (noetic["ready_know_threshold"], noetic["ready_uncertainty_threshold"])
```

**Underlying source:** `dynamic_thresholds.py:268,355` SELECT against `calibration_trajectory` table.

**Conclusion:** No code change needed. See Discovery 4 for why.

---

## Discovery 3: CHECK and POSTFLIGHT shared call site

**Verified.** Both phases route through `_run_single_phase_verification` via the wrapper `run_grounded_verification_pipeline()` at `empirica/core/post_test/grounded_calibration.py:720`.

**Dispatch sites in the wrapper:**
- Line 772: noetic phase verification (when phase_boundary present)
- Line 790: praxic phase verification
- Line 809: combined phase verification (when no phase_boundary)

All three call `_run_single_phase_verification(...)` directly. **The consistency test in plan Task 16 is supported** — CHECK and POSTFLIGHT for the same `work_type` will produce identical `calibration_status` because they share the same execution path.

---

## Discovery 4: Tasks 12, 13, 14 are no-ops (architectural property)

**The critical insight:** the new threshold gate and remote-ops short-circuit (Tasks 10/11) early-return from `_run_single_phase_verification` BEFORE any storage operations.

**Storage operations in `_run_single_phase_verification` (after the early return point):**
| Line | Operation | Table |
|------|-----------|-------|
| 638 | `manager.store_evidence(bundle)` | `evidence_*` |
| 639 | `manager.store_verification(...)` | `grounded_verifications` |
| 646 | `tracker.record_trajectory_point(...)` | `calibration_trajectory` |

**Call site analysis:**
- `store_verification` — defined at `grounded_calibration.py:306`, called from EXACTLY ONE site at line 639 (verified via grep)
- `record_trajectory_point` — defined at `trajectory_tracker.py:56`, called from EXACTLY ONE site at line 646 (verified via grep)

**Therefore:**
- Non-grounded transactions (insufficient_evidence, ungrounded_remote_ops) never reach storage
- `grounded_verifications` table only contains grounded data
- `calibration_trajectory` table only contains grounded data
- `previous_transaction_feedback` query (Discovery 1) is naturally clean
- `dynamic_thresholds` queries (Discovery 2) are naturally clean
- `sentinel-gate.py` metacog reads are naturally clean

**Plan tasks affected:**
- ❌ **Task 12** (trajectory_tracker guard) — DROP. No other call site exists; the early return is sufficient.
- ❌ **Task 13** (workflow_commands.py SELECT filter) — DROP. The query naturally excludes non-grounded.
- ❌ **Task 14** (sentinel-gate.py guard) — DROP. The dynamic_thresholds reads are naturally clean.

**Tests still valuable:**
- ✅ `test_empty_bundle_returns_insufficient_status` (Task 10 Step 5) — verifies the architectural property holds.
- ✅ The remote-ops integration test (Task 16) — verifies trajectory rows are not written for ungrounded transactions.

---

## Discovery 5: Legacy SQLite calibration row reads — no migration needed

12 files reference `calibration_trajectory` or `grounded_verifications`. Per Discovery 4, both tables only contain grounded data (now and historically). No `calibration_status` column needs to be added, no read-time-default decision needed, no backfill migration required.

If a future change wants to query non-grounded transactions explicitly (e.g., for debugging or histograms), add the column then. YAGNI for this implementation.

---

## Plan scope reduction

| Original | After T0 |
|---|---|
| 17 tasks across 8 phases | **14 tasks** across **6 phases** |
| 10 implementation transactions | **8 transactions** (T0 + T1-T7) |

**Dropped:** Task 12, Task 13, Task 14 (and the originally proposed transactions T6 and T7 from the brainstorming session).

**Updated transaction grouping:**

| TX | Phase | Tasks | work_type | Goal |
|---|---|---|---|---|
| T0 | Discovery | 0 | audit | ✅ COMPLETE |
| T1 | Phase 1 | 1, 2, 3 | code | Foundation data structures |
| T2 | Phase 2 | 4, 5 | code | remote-ops work_type entry + verification |
| T3 | Phase 3 | 6, 7, 8 | code | EvidenceProfile.INSUFFICIENT path |
| T4 | Phase 4 | 9 | code | source_errors + sources_empty capture |
| T5 | Phase 5 | 10, 11 | code | Threshold gate + remote-ops short-circuit |
| T6 | Phase 7 | 15 | docs | Documentation surface updates |
| T7 | Phase 8 | 16, 17 | code | Integration test + manual smoke test |

---

## Sources

- `empirica/cli/command_handlers/workflow_commands.py` (read)
- `empirica/plugins/claude-code-integration/hooks/sentinel-gate.py` (read)
- `empirica/core/post_test/grounded_calibration.py` (read)
- `empirica/core/post_test/dynamic_thresholds.py` (grep)
- `empirica/core/post_test/trajectory_tracker.py` (grep)
