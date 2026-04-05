# Code Quality Plan: F/E Average → B-C Target

**Created:** 2026-04-01
**Updated:** 2026-04-06
**Current state:** 13 F-grade functions, 3097 ruff issues (down from 8343). Average F/98 for worst functions.
**Target:** No F-grade functions, E-grade only where justified. Average C/15-20.
**Tests:** 582 pass, 5 skip.

---

## Completed

- T0.1: Ruff auto-fix (8343 → 3097, -63%)
- T1.1: is_safe_bash_command E/35 → C/16 (table-driven + 4 helpers)
- T1.x: sentinel main F/139 → F/73 (9 extracted helpers)
- UP045: 1121 Optional→union conversions auto-fixed

## Current F-Grade Functions (13)

| Function | File | CC | Notes |
|---|---|---|---|
| handle_postflight_submit_command | workflow_commands.py | F/224 | Grew (memory pipeline added) |
| handle_project_bootstrap_command | project_bootstrap.py | F/203 | Unchanged |
| handle_setup_claude_code_command | setup_claude_code.py | F/142 | Unchanged |
| handle_check_submit_command | workflow_commands.py | F/122 | Unchanged |
| handle_preflight_submit_command | workflow_commands.py | F/91 | Unchanged |
| handle_session_create_command | session_create.py | F/88 | Unchanged |
| sentinel main() | sentinel-gate.py | F/73 | Improved from F/139 |
| handle_goals_create_command | goal_commands.py | F/70 | Unchanged |
| handle_calibration_report_command | monitor_commands.py | F/70 | Unchanged |
| handle_project_switch_command | project_commands.py | F/63 | Unchanged |
| _try_increment_tool_count | sentinel-gate.py | F/46 | Grew (tool trace) |
| generate_suggestions | workflow_patterns.py | F/46 | NEW |
| export_grounded_calibration | grounded_calibration.py | F/41 | Unchanged |

---

## Next Transactions (Priority Order)

### TX1: Dedup and extract shared workflow helpers
- handle_preflight/check/postflight share: input parsing, session resolution, transaction reading, sentinel invocation, checkpoint creation
- Extract shared `WorkflowContext` dataclass + pipeline stages
- **Impact:** All 3 handlers shrink by ~30-40 CC each
- **Effort:** 2 transactions
- **Target:** Each handler drops 1 grade (F→E or E→D)

### TX2: handle_postflight_submit_command F/224
- Worst function. Pipeline: parse → resolve → read tx → create checkpoint → grounded verification → Qdrant embed → episodic memory → workspace index → memory hot-cache → promotion → demotion → eviction → retrospective → Cortex push → format output
- Extract: `_run_grounded_verification()`, `_run_qdrant_pipeline()`, `_run_memory_pipeline()`, `_build_postflight_output()`
- **Target:** E/35-40 (legitimate complexity in verification + calibration)
- **Effort:** 1-2 transactions

### TX3: handle_project_bootstrap_command F/203
- 20+ sequential steps loading project context
- Pipeline pattern: `stages = [load_session, load_project, load_goals, ...]`
- **Target:** D/25
- **Effort:** 1-2 transactions

### TX4: handle_setup_claude_code_command F/142
- Sequential setup steps (create dirs, copy hooks, write settings, configure MCP)
- Pipeline: `setup_stages = [create_dirs, install_plugin, configure_hooks, ...]`
- **Target:** D/25
- **Effort:** 1 transaction

### TX5: generate_suggestions F/46
- 5 analysis blocks that follow the same pattern
- Table-driven: `ANALYSES = [(name, detector_fn, formatter_fn), ...]`
- **Target:** C/15
- **Effort:** 1 transaction

### TX6: Remaining E-grade cleanup
- Batch extract helpers from remaining D/E functions
- **Effort:** 2-3 transactions

---

## Ruff Remaining (3097 → target <500)

| Rule | Count | Action |
|---|---|---|
| W293 (whitespace in blank lines) | 530 | Auto-fix safe |
| F541 (empty f-strings) | 384 | Auto-fix safe |
| RUF013 (implicit optional) | 247 | Needs UP045 already done |
| F405 (star imports) | 145 | Structural — split __init__.py |
| C901 (complexity) | 108 | Addressed by TX1-TX6 |
| E402 (import order) | 59 | Many intentional (lazy imports) |
| F841 (unused vars) | 57 | Manual review needed |
| SIM105 (contextlib.suppress) | 55 | Auto-fix safe |

### Next ruff transaction:
- Auto-fix: W293, F541, SIM105 (~969 issues)
- Manual: F841 unused vars (~57 issues)
- **Target:** 3097 → ~2000

---

## Verification

After each transaction:
```bash
radon cc empirica/ -s -a --min D 2>&1 | grep "Average"
radon cc empirica/ -s --min F 2>&1 | wc -l
ruff check empirica/ --statistics 2>&1 | tail -1
python -m pytest tests/ -x -q
```
