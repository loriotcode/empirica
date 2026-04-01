# Code Quality Plan: F/E Average → B-C Target

**Created:** 2026-04-01
**Current state:** 118 functions at D+ (24 F-grade, 23 E-grade, 71 D-grade). Average: E/38.
**Target:** No F-grade functions, E-grade only where justified. Average: C/15-20.

---

## Tier 0: Mechanical Fixes (No Risk, High Volume)

### T0.1: Ruff Unsafe Fixes (1048 remaining auto-fixable)
- Run `ruff check --unsafe-fixes --fix` with test verification
- Covers: SIM114 (if-same-arms), F811 (redefined-unused), RUF021 (chained ops)
- **Effort:** 1 transaction, ~30 min
- **Impact:** 3000 → ~1952 issues

### T0.2: Silent Exception Cleanup (77 instances across 57 files)
- Replace `except Exception: pass` with either:
  - `except Exception: logger.debug(...)` (if swallowing is intentional)
  - Remove the try/except (if the exception should propagate)
  - Add specific exception types
- **Effort:** 2-3 transactions (batch by module: hooks, CLI, core)
- **Impact:** Reliability ++, debugging becomes possible

---

## Tier 1: Table-Driven Refactors (Low Risk, Targeted)

These F/E functions are complex because of long if/elif chains that should be lookup tables.

### T1.1: is_safe_bash_command() E/35 → target B/8
- Currently 35 nested if/elif checks
- Convert to: `SAFE_PREFIXES = {"git ", "ls ", "cat ", ...}` + `any(cmd.startswith(p) for p in SAFE_PREFIXES)`
- **File:** sentinel-gate.py (hooks + repo copy)
- **Effort:** 1 transaction

### T1.2: _classify_rsync D/21 → target B/8
- Same pattern: nested conditions → lookup table
- **File:** sentinel-gate.py

### T1.3: is_transition_command C/12 → target A/3
- Already near-table, just needs cleanup
- **File:** sentinel-gate.py

### T1.4: generate_context_markdown E/36 → target C/12
- Long if/elif building markdown sections → list of (condition, renderer) tuples
- **File:** empirica/data/formatters/context_formatter.py

---

## Tier 2: Pipeline Pattern (Medium Risk, Architectural)

These F-grade handlers are sequential pipelines that do 5-8 things in order. The fix is to make the pipeline explicit.

### T2.1: workflow_commands.py Pipeline (3 functions, combined F/391)
```
handle_*_command(args):
    ctx = parse_input(args, phase)        # shared helper (done)
    ctx = resolve_session(ctx)            # shared helper (done)
    ctx = read_transaction_state(ctx)     # NEW: returns WorkflowContext
    ctx = create_checkpoint(ctx)          # NEW: GitEnhancedReflexLogger
    ctx = invoke_sentinel(ctx)            # shared helper (done)
    ctx = phase_specific_logic(ctx)       # KEEP INLINE (unique per handler)
    ctx = run_calibration(ctx)            # NEW: Bayesian updates
    return format_response(ctx)           # NEW: JSON assembly
```
Each stage is a function taking+returning a typed context dict.
- **Target:** Each handler D/25, each stage B-C/8-15
- **Effort:** 3 transactions (one per handler), risk: medium (test after each)

### T2.2: handle_project_bootstrap_command F/203
- The single worst function. Loads project context in ~20 sequential steps.
- Same pipeline pattern: `bootstrap_stages = [load_session, load_project, load_goals, ...]`
- **Effort:** 2 transactions
- **Target:** D/25

### T2.3: handle_setup_claude_code_command F/136
- Creates plugin directory structure with many sequential steps
- Pipeline: `setup_stages = [create_dirs, copy_hooks, write_settings, ...]`
- **Effort:** 1-2 transactions
- **Target:** D/25

---

## Tier 3: Structural Splits (Higher Risk, File-Level)

These need new files, not just new functions.

### T3.1: session_resolver.py → split into 3 modules
- Currently 1800+ lines, 3 E-grade + 1 D-grade functions
- Split: `session_resolver.py` (core resolution), `transaction_state.py` (transaction + counters), `instance_resolver.py` (the class)
- **Effort:** 2 transactions
- **Risk:** Many importers need updating

### T3.2: sentinel-gate.py main() F/104 → continued extraction
- Already extracted 4 helpers. Next targets:
  - Block 13 (no-PREFLIGHT handler) → helper
  - Block 16 already extracted
  - Block 17 (anti-gaming) → helper
  - Block 9 (closed-transaction short-circuit) → helper
- **Target:** D/30
- **Effort:** 1-2 transactions

### T3.3: collector.py split
- PostTestCollector has 5 D-grade methods, all doing different evidence collection
- Split into: `git_collector.py`, `code_quality_collector.py`, `artifact_collector.py`
- **Effort:** 2 transactions

---

## Execution Order (Dependencies)

```
T0.1 (ruff unsafe) ──→ no deps, do first
T0.2 (except:pass) ──→ no deps, parallel with T0.1
T1.1-T1.4 (tables) ──→ no deps, parallel
T2.1 (workflow pipeline) ──→ after T0 (clean baseline)
T2.2 (bootstrap) ──→ independent
T2.3 (setup) ──→ independent
T3.1 (session_resolver split) ──→ after T2.1 (shares helpers)
T3.2 (sentinel continued) ──→ after T1.1 (is_safe_bash first)
T3.3 (collector split) ──→ independent
```

## Scoring Estimate

| Tier | Functions Improved | Before (avg) | After (target) | Transactions |
|------|-------------------|--------------|----------------|-------------|
| T0 | 0 (quality only) | — | — | 3-4 |
| T1 | 4 | E/30 | B/8 | 2-3 |
| T2 | 5 | F/146 | D/25 | 6-8 |
| T3 | 8+ | E/31 | C/15 | 5-6 |
| **Total** | **17+** | **E/38 avg** | **C/18 avg** | **16-21** |

## What Won't Reach B-C

Some functions are legitimately complex and will stay D:
- `handle_postflight_submit_command` — even with pipeline, the phase-specific logic (grounded calibration, Bayesian updates, retrospective) is inherently D/25+
- `main()` in sentinel-gate — even fully extracted, the decision tree is D/25+ because it has 15+ legitimate decision points
- `handle_project_bootstrap_command` — loading project context has many valid sequential steps

The goal isn't A/B everywhere — it's eliminating F, reducing E, and making D the justified ceiling.

## Verification

After each tier, run:
```bash
radon cc empirica/ -s -a --min D 2>&1 | grep "Average"  # Track average
radon cc empirica/ -s --min F 2>&1 | wc -l              # Count F-grade
ruff check empirica/ --statistics 2>&1 | tail -1         # Track issues
python -m pytest tests/ -x -q                            # No regressions
```
