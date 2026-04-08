# Sentinel Measurer: `remote-ops` + Insufficient-Evidence Hardening — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `remote-ops` work_type as an "on/off switch" for ungroundable work, and harden the Sentinel measurer to refuse measurement (rather than emit phantom scores) when grounded coverage is insufficient.

**Architecture:** Three layers of change to the post-test verification pipeline. (1) Data layer: add `calibration_status` field, `EvidenceProfile.INSUFFICIENT`, `sources_empty`/`source_errors` capture. (2) Logic layer: zero-relevance entry for `remote-ops` in `WORK_TYPE_RELEVANCE`, coverage threshold gate in `_run_single_phase_verification`. (3) Glue layer: sentinel-gate hook handles new statuses, trajectory_tracker skips non-grounded writes, doc surfaces updated. Existing `grounded` calibration path remains unchanged.

**Tech Stack:** Python 3.11+, pytest, pydantic v2, sqlite3 (existing empirica stack). No new dependencies.

**Spec:** `docs/superpowers/specs/2026-04-08-sentinel-measurer-remote-ops-design.md`

---

## File Structure

| File | Role | Change Type |
|---|---|---|
| `empirica/cli/validation.py` | PreflightInput model w/ work_type regex | Modify |
| `empirica/core/post_test/mapper.py` | `WORK_TYPE_RELEVANCE`, `GroundedAssessment` dataclass | Modify |
| `empirica/core/post_test/collector.py` | `EvidenceProfile`, `EvidenceBundle`, `_resolve_profile`, `collect_all` | Modify |
| `empirica/core/post_test/grounded_calibration.py` | `_run_single_phase_verification` (threshold + remote-ops gates) | Modify |
| `empirica/core/post_test/trajectory_tracker.py` | `record_trajectory_point` (skip non-grounded) | Modify |
| `empirica/plugins/claude-code-integration/hooks/sentinel-gate.py` | Handle `calibration_status` field | Modify (after locating call site) |
| `empirica/cli/command_handlers/workflow_commands.py` OR similar | `previous_transaction_feedback` aggregation | Modify (after locating call site) |
| `empirica/plugins/claude-code-integration/templates/CLAUDE.md` | work_type enum doc | Modify |
| `empirica/plugins/claude-code-integration/skills/epistemic-transaction/SKILL.md` | work_type list | Modify |
| `empirica/plugins/claude-code-integration/skills/empirica-constitution/SKILL.md` | Routing rule for remote-ops | Modify |
| `tests/core/post_test/test_remote_ops.py` | New test module (all remote-ops + integration tests) | Create |
| `tests/core/post_test/test_collector_hardening.py` | New test module (profile, source_errors) | Create |
| `tests/core/post_test/test_work_type_exclusion.py` | Add ~2 tests for `remote-ops` relevance + `GroundedAssessment.calibration_status` | Modify (existing) |
| `tests/core/post_test/test_grounded_calibration_threshold.py` | New test module (threshold gate, status, trajectory guard) | Create |
| `tests/unit/cli/test_validation.py` | New test module (work_type regex) | Create |

---

## Pre-Implementation: Discovery Tasks

These resolve the reviewer's advisory items before locking the implementation.

### Task 0: Locate ambiguous call sites

**Files:** discovery-only, no edits

- [ ] **Step 1: Find `previous_transaction_feedback` aggregation site**

```bash
cd /home/yogapad/empirical-ai/empirica
grep -rn "previous_transaction_feedback" empirica/ --include="*.py"
grep -rn "overestimate_tendency" empirica/ --include="*.py"
ls empirica/cli/command_handlers/workflow_commands.py 2>&1
```

Expected: identifies the file/function that builds the feedback dict shown at PREFLIGHT response. Strong candidate is `empirica/cli/command_handlers/workflow_commands.py` (confirmed to exist by spec review — has tests under `tests/unit/cli/test_workflow_commands_delta_fix.py`). **Record the exact file path AND function name.**

- [ ] **Step 2: Find metacog state read/write in sentinel-gate.py**

```bash
grep -n "metacog\|brier\|calibration_score\|holistic_calibration" \
  empirica/plugins/claude-code-integration/hooks/sentinel-gate.py
```

Expected: identifies where calibration data is consumed by the gate. **Record line numbers.**

- [ ] **Step 3: Verify CHECK and POSTFLIGHT share `_run_single_phase_verification`**

```bash
grep -rn "_run_single_phase_verification" empirica/ --include="*.py"
```

Expected: both `check-submit` and `postflight-submit` command handlers route through the same function. The function is at `empirica/core/post_test/grounded_calibration.py:594` and is called from 3 dispatch sites in the same file (around lines 772, 790, 809). If both phases share this function, the consistency test (`test_remote_ops_check_phase_consistent_with_postflight`) is supported. **Record findings.**

> **Naming note:** the function is named `_run_single_phase_verification` everywhere in this plan. Don't shorten it.

- [ ] **Step 4: Find legacy SQLite calibration row reads**

```bash
grep -rn "calibration_status\|calibration_trajectory" empirica/ --include="*.py" | head -20
```

Expected: identifies whether reads default missing `calibration_status` to `"grounded"` at the query mapper level OR whether we need a backfill migration. **Decision recorded.**

- [ ] **Step 5: Commit discovery findings as a markdown note**

```bash
cat > docs/superpowers/plans/2026-04-08-discovery-notes.md <<'EOF'
# Discovery findings for sentinel-measurer-remote-ops plan

- previous_transaction_feedback location: <file>:<line>
- sentinel-gate.py metacog call sites: <line numbers>
- CHECK/POSTFLIGHT shared call site: <yes/no, function name>
- Legacy calibration_status: <read-time-default vs backfill decision>
EOF

# Commit the discovery notes — docs/superpowers/plans/ is NOT gitignored
# (only docs/superpowers/specs/ is). The notes file is a real artifact.
git add docs/superpowers/plans/2026-04-08-discovery-notes.md
git commit -m "docs(plan): discovery notes for sentinel-measurer-remote-ops plan"
```

---

## Phase 1: Foundation Data Structures

### Task 1: Add `remote-ops` to PreflightInput regex

**Files:**
- Modify: `empirica/cli/validation.py:53-57`
- Test (CREATE NEW): `tests/unit/cli/test_validation.py`

- [ ] **Step 1: Create the new test file with the failing tests**

```python
# tests/unit/cli/test_validation.py — NEW FILE
"""Tests for empirica.cli.validation Pydantic models."""

import pytest
from pydantic import ValidationError
from empirica.cli.validation import PreflightInput


def test_preflight_input_accepts_remote_ops_work_type():
    """work_type=remote-ops should validate successfully."""
    payload = {
        "session_id": "abc",
        "vectors": {"know": 0.5, "uncertainty": 0.5},
        "work_type": "remote-ops",
    }
    model = PreflightInput(**payload)
    assert model.work_type == "remote-ops"


def test_preflight_input_rejects_unknown_work_type():
    """An unknown work_type should still be rejected."""
    payload = {
        "session_id": "abc",
        "vectors": {"know": 0.5, "uncertainty": 0.5},
        "work_type": "garbage",
    }
    with pytest.raises(ValidationError):
        PreflightInput(**payload)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/yogapad/empirical-ai/empirica
pytest tests/unit/cli/test_validation.py::test_preflight_input_accepts_remote_ops_work_type -v
```

Expected: FAIL with `ValidationError: String should match pattern '^(code|infra|...|audit)$'`

- [ ] **Step 3: Update the regex in validation.py**

Change line 56:
```python
# OLD:
pattern="^(code|infra|research|release|debug|config|docs|data|comms|design|audit)$",
# NEW:
pattern="^(code|infra|research|release|debug|config|docs|data|comms|design|audit|remote-ops)$",
```

Also extend the `description` field:
```python
description="Type of work being done — determines which evidence sources are relevant for grounded calibration. Use 'remote-ops' for work on machines the local Sentinel doesn't observe (SSH, customer machines, remote config).",
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/unit/cli/test_validation.py -v -k "work_type"
```

Expected: PASS for both new tests, no regressions.

- [ ] **Step 5: Commit**

```bash
git add empirica/cli/validation.py tests/unit/cli/test_validation.py
git commit -m "feat(validation): add remote-ops work_type to PreflightInput regex"
```

---

### Task 2: Add `calibration_status` field to `GroundedAssessment`

**Files:**
- Modify: `empirica/core/post_test/mapper.py:170-194`
- Test: `tests/core/post_test/test_work_type_exclusion.py` (existing file — add tests at the end)

- [ ] **Step 1: Write the failing test (append to existing test file)**

```python
# tests/core/post_test/test_work_type_exclusion.py — APPEND
def test_grounded_assessment_has_calibration_status_field():
    """GroundedAssessment dataclass should expose calibration_status."""
    from empirica.core.post_test.mapper import GroundedAssessment
    assessment = GroundedAssessment(
        session_id="test-session",
        self_assessed={"know": 0.7},
        grounded={},
        calibration_gaps={},
        grounded_coverage=0.0,
        overall_calibration_score=0.0,
    )
    assert hasattr(assessment, "calibration_status")
    assert assessment.calibration_status == "grounded"  # default


def test_grounded_assessment_calibration_status_explicit():
    """calibration_status can be set explicitly."""
    from empirica.core.post_test.mapper import GroundedAssessment
    assessment = GroundedAssessment(
        session_id="test-session",
        self_assessed={"know": 0.7},
        grounded={},
        calibration_gaps={},
        grounded_coverage=0.0,
        overall_calibration_score=0.0,
        calibration_status="ungrounded_remote_ops",
    )
    assert assessment.calibration_status == "ungrounded_remote_ops"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/core/post_test/test_work_type_exclusion.py::test_grounded_assessment_has_calibration_status_field -v
```

Expected: FAIL with `AttributeError: 'GroundedAssessment' object has no attribute 'calibration_status'`

- [ ] **Step 3: Add the field to GroundedAssessment**

In `empirica/core/post_test/mapper.py`, modify the `GroundedAssessment` dataclass (around line 171-194):

```python
@dataclass
class GroundedAssessment:
    """Complete grounded assessment alongside self-assessment.

    insufficient_evidence_vectors lists vector names that had ALL their
    evidence sources excluded for the current work_type — meaning the
    instrument is fundamentally blind to this kind of work for these vectors.
    These vectors are NOT in `grounded`, NOT in `calibration_gaps`, and
    should NOT be written to calibration_trajectory or used to update
    overestimate_tendency advisories. The AI's self-assessed value stands
    as the best available estimate, with no false drift.

    calibration_status indicates whether the calibration produced meaningful
    grounded data:
    - "grounded": normal calibration with sufficient evidence
    - "insufficient_evidence": grounded_coverage below INSUFFICIENT_EVIDENCE_THRESHOLD
    - "ungrounded_remote_ops": work_type=remote-ops, no local sources by design
    Only "grounded" status writes to learning_trajectory or feeds
    previous_transaction_feedback.
    """
    session_id: str
    self_assessed: dict[str, float]
    grounded: dict[str, GroundedVectorEstimate]
    calibration_gaps: dict[str, float]
    grounded_coverage: float
    overall_calibration_score: float
    phase: str = "combined"
    insufficient_evidence_vectors: list[str] = None  # type: ignore[assignment]
    calibration_status: str = "grounded"  # NEW

    def __post_init__(self):
        if self.insufficient_evidence_vectors is None:
            self.insufficient_evidence_vectors = []
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/core/post_test/test_work_type_exclusion.py -v -k "calibration_status"
```

Expected: PASS for both new tests.

- [ ] **Step 5: Commit**

```bash
git add empirica/core/post_test/mapper.py tests/core/post_test/test_work_type_exclusion.py
git commit -m "feat(mapper): add calibration_status field to GroundedAssessment"
```

---

### Task 3: Add `sources_empty` and `source_errors` to `EvidenceBundle`

**Files:**
- Modify: `empirica/core/post_test/collector.py:61-69`
- Test (CREATE NEW): `tests/core/post_test/test_collector_hardening.py`

- [ ] **Step 1: Create the new test file with the failing test**

```python
# tests/core/post_test/test_collector_hardening.py — NEW FILE
"""Tests for the collector's insufficient-evidence and source-error
handling — the 'fail loudly' layer added in the remote-ops design.
"""

import pytest
from empirica.core.post_test.collector import (
    EvidenceBundle, EvidenceProfile, PostTestCollector,
)


def test_evidence_bundle_has_sources_empty_and_source_errors():
    """EvidenceBundle should expose sources_empty list and source_errors dict."""
    bundle = EvidenceBundle(session_id="test")
    assert hasattr(bundle, "sources_empty")
    assert hasattr(bundle, "source_errors")
    assert bundle.sources_empty == []
    assert bundle.source_errors == {}
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/core/post_test/test_collector_hardening.py::test_evidence_bundle_has_sources_empty_and_source_errors -v
```

Expected: FAIL with `AttributeError`

- [ ] **Step 3: Add the fields to EvidenceBundle**

In `empirica/core/post_test/collector.py` (around line 61-69):

```python
@dataclass
class EvidenceBundle:
    """Complete evidence collection for a session."""
    session_id: str
    items: list[EvidenceItem] = field(default_factory=list)
    collection_timestamp: float = 0.0
    sources_available: list[str] = field(default_factory=list)
    sources_failed: list[str] = field(default_factory=list)
    sources_empty: list[str] = field(default_factory=list)  # NEW: ran ok, returned 0 items
    source_errors: dict[str, str] = field(default_factory=dict)  # NEW: type+msg per failed source
    coverage: float = 0.0
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/core/post_test/test_collector_hardening.py::test_evidence_bundle_has_sources_empty_and_source_errors -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add empirica/core/post_test/collector.py tests/core/post_test/test_collector_hardening.py
git commit -m "feat(collector): add sources_empty and source_errors to EvidenceBundle"
```

---

## Phase 2: `remote-ops` Work Type Logic

### Task 4: Add `WORK_TYPE_RELEVANCE["remote-ops"]` entry

**Files:**
- Modify: `empirica/core/post_test/mapper.py:78-156`
- Test: `tests/core/post_test/test_work_type_exclusion.py` (existing — append)

- [ ] **Step 1: Write the failing test (append to existing test file)**

```python
# tests/core/post_test/test_work_type_exclusion.py — APPEND
def test_remote_ops_relevance_zeros_all_sources():
    """remote-ops work_type should have all evidence sources at 0.0 relevance."""
    from empirica.core.post_test.mapper import WORK_TYPE_RELEVANCE
    assert "remote-ops" in WORK_TYPE_RELEVANCE
    weights = WORK_TYPE_RELEVANCE["remote-ops"]
    # Every weight present must be 0.0
    for source, weight in weights.items():
        assert weight == 0.0, f"remote-ops {source} should be 0.0, got {weight}"
    # Check all known sources are present so nothing slips through with default 1.0
    expected_sources = {
        "artifacts", "noetic", "sentinel", "goals", "issues", "triage",
        "codebase_model", "non_git_files", "git", "code_quality",
        "pytest", "source_quality", "prose_quality", "document_metrics",
        "action_verification", "web",
    }
    assert expected_sources <= set(weights.keys()), \
        f"Missing sources: {expected_sources - set(weights.keys())}"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/core/post_test/test_work_type_exclusion.py::test_remote_ops_relevance_zeros_all_sources -v
```

Expected: FAIL with `assert "remote-ops" in WORK_TYPE_RELEVANCE`

- [ ] **Step 3: Add the entry to WORK_TYPE_RELEVANCE**

In `empirica/core/post_test/mapper.py`, add inside the `WORK_TYPE_RELEVANCE` dict (after the existing `"audit"` entry around line 156):

```python
    # remote-ops: work done on a machine the local Sentinel doesn't observe
    # (SSH sessions, customer machines, remote config, deploys without local
    # commits, on-site assistance). No source can ground vectors for this work.
    # The AI's self-assessment stands unchallenged. FUTURE: a RemoteVerifier
    # agent on target machines posting EvidenceItem[] back via the dispatch
    # bus — at which point relevance can be reintroduced.
    "remote-ops": {
        "artifacts": 0.0,
        "noetic": 0.0,
        "sentinel": 0.0,
        "goals": 0.0,
        "issues": 0.0,
        "triage": 0.0,
        "codebase_model": 0.0,
        "non_git_files": 0.0,
        "git": 0.0,
        "code_quality": 0.0,
        "pytest": 0.0,
        "source_quality": 0.0,
        "prose_quality": 0.0,
        "document_metrics": 0.0,
        "action_verification": 0.0,
        "web": 0.0,
    },
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/core/post_test/test_work_type_exclusion.py::test_remote_ops_relevance_zeros_all_sources -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add empirica/core/post_test/mapper.py tests/core/post_test/test_work_type_exclusion.py
git commit -m "feat(mapper): add remote-ops work_type with all-zero relevance"
```

---

### Task 5: Verify remote-ops routes vectors to insufficient_evidence_vectors

**Files:**
- Test only: `tests/core/post_test/test_remote_ops.py` (NEW file)

This task verifies the existing exclusion logic at `mapper.py:402-410` and `:442-444` cleanly handles the all-zero relevance entry from Task 4. No production code change.

- [ ] **Step 1: Create new test file with end-to-end mapper test**

```python
# tests/core/post_test/test_remote_ops.py — NEW FILE
"""Tests for remote-ops work_type — the on/off switch for ungroundable work."""

import pytest
from empirica.core.post_test.mapper import (
    EvidenceMapper, GroundedAssessment,
)
from empirica.core.post_test.collector import (
    EvidenceBundle, EvidenceItem, EvidenceQuality,
)


def _make_bundle_with_evidence(session_id="rops-test"):
    """Build a bundle with evidence touching multiple vectors."""
    bundle = EvidenceBundle(session_id=session_id)
    bundle.items = [
        EvidenceItem(
            source="artifacts",
            metric_name="findings_logged",
            value=0.7,
            raw_value={"count": 5},
            quality=EvidenceQuality.OBJECTIVE,
            supports_vectors=["know", "signal"],
        ),
        EvidenceItem(
            source="noetic",
            metric_name="investigation_depth",
            value=0.6,
            raw_value={"depth": 3},
            quality=EvidenceQuality.OBJECTIVE,
            supports_vectors=["context", "do"],
        ),
        EvidenceItem(
            source="git",
            metric_name="lines_changed",
            value=0.5,
            raw_value={"lines": 100},
            quality=EvidenceQuality.OBJECTIVE,
            supports_vectors=["change", "state"],
        ),
    ]
    bundle.sources_available = ["artifacts", "noetic", "git"]
    return bundle


def test_remote_ops_excludes_all_evidence_sources():
    """work_type=remote-ops should leave grounded dict empty and flag all
    seen vectors as insufficient_evidence."""
    bundle = _make_bundle_with_evidence()
    self_assessed = {
        "know": 0.8, "signal": 0.7, "context": 0.6,
        "do": 0.7, "change": 0.5, "state": 0.6, "uncertainty": 0.3,
    }

    mapper = EvidenceMapper()
    assessment = mapper.map_evidence(
        bundle, self_assessed, phase="combined",
        domain="default", work_type="remote-ops",
    )

    # No grounded values, no gaps
    assert assessment.grounded == {}
    assert assessment.calibration_gaps == {}
    # All vectors that had evidence should land in insufficient
    assert "know" in assessment.insufficient_evidence_vectors
    assert "signal" in assessment.insufficient_evidence_vectors
    assert "context" in assessment.insufficient_evidence_vectors
    assert "do" in assessment.insufficient_evidence_vectors
    assert "change" in assessment.insufficient_evidence_vectors
    assert "state" in assessment.insufficient_evidence_vectors


def test_remote_ops_self_assessed_vectors_unchanged():
    """The self_assessed dict on the assessment matches what was passed in."""
    bundle = _make_bundle_with_evidence()
    self_assessed = {"know": 0.8, "uncertainty": 0.2}
    mapper = EvidenceMapper()
    assessment = mapper.map_evidence(
        bundle, self_assessed, phase="combined",
        work_type="remote-ops",
    )
    assert assessment.self_assessed == self_assessed
```

- [ ] **Step 2: Run tests — they should PASS without further code changes**

```bash
pytest tests/core/post_test/test_remote_ops.py -v
```

Expected: PASS for both tests. (If they fail, the existing exclusion logic at `mapper.py:402-410` is broken — investigate before continuing.)

- [ ] **Step 3: Commit**

```bash
git add tests/core/post_test/test_remote_ops.py
git commit -m "test(mapper): verify remote-ops routes vectors to insufficient_evidence"
```

---

## Phase 3: Profile Detection Hardening

### Task 6: Add `EvidenceProfile.INSUFFICIENT` value

**Files:**
- Modify: `empirica/core/post_test/collector.py:79-100`
- Test: `tests/core/post_test/test_collector_hardening.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/core/post_test/test_collector_hardening.py — add
def test_evidence_profile_has_insufficient_value():
    """EvidenceProfile should expose INSUFFICIENT alongside CODE/PROSE/etc."""
    from empirica.core.post_test.collector import EvidenceProfile
    assert hasattr(EvidenceProfile, "INSUFFICIENT")
    assert EvidenceProfile.INSUFFICIENT == "insufficient"
    assert EvidenceProfile.INSUFFICIENT in EvidenceProfile.VALID
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/core/post_test/test_collector_hardening.py::test_evidence_profile_has_insufficient_value -v
```

Expected: FAIL with `AttributeError`

- [ ] **Step 3: Add the value**

In `empirica/core/post_test/collector.py` modify the `EvidenceProfile` class:

```python
class EvidenceProfile:
    """Evidence collection profile — determines which collectors run.

    Profiles:
    - "code": ruff, radon, pyright, pytest, git (default for repos with .py files)
    - "prose": textstat, proselint, vale, document metrics, source quality
    - "web": build verification, HTML validation, link integrity, terminology, assets
    - "hybrid": all evidence sources (code + prose + web when detected)
    - "insufficient": no measurable signal — only universal collectors run, AI
      self-assessment is the authority for this transaction. Used when out-of-repo
      work or no changed files at all.
    - "auto": detect from project content (falls back to "insufficient" if no signal)

    Set via: project.yaml: evidence_profile: web
             CLI flag: --evidence-profile web
             Environment: EMPIRICA_EVIDENCE_PROFILE=web
    """
    CODE = "code"
    PROSE = "prose"
    WEB = "web"
    HYBRID = "hybrid"
    INSUFFICIENT = "insufficient"  # NEW
    AUTO = "auto"

    VALID = {CODE, PROSE, WEB, HYBRID, INSUFFICIENT, AUTO}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/core/post_test/test_collector_hardening.py::test_evidence_profile_has_insufficient_value -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add empirica/core/post_test/collector.py tests/core/post_test/test_collector_hardening.py
git commit -m "feat(collector): add EvidenceProfile.INSUFFICIENT enum value"
```

---

### Task 7: `_resolve_profile()` returns INSUFFICIENT for empty changed files

**Files:**
- Modify: `empirica/core/post_test/collector.py:199-219`
- Test: `tests/core/post_test/test_collector_hardening.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/core/post_test/test_collector_hardening.py — add
def test_resolve_profile_returns_insufficient_when_no_changed_files(monkeypatch):
    """When AUTO and no changed files, profile should be INSUFFICIENT not PROSE."""
    from empirica.core.post_test.collector import (
        PostTestCollector, EvidenceProfile,
    )

    collector = PostTestCollector(session_id="test", phase="praxic")
    # Stub out the changed-files lookup
    monkeypatch.setattr(collector, "_get_session_changed_files", lambda: [])
    monkeypatch.setattr(collector, "evidence_profile", "auto")

    profile = collector._resolve_profile()
    assert profile == EvidenceProfile.INSUFFICIENT


def test_resolve_profile_still_returns_prose_when_only_markdown_changed(monkeypatch):
    """Markdown/text files (not .py, not web) should still produce PROSE."""
    from empirica.core.post_test.collector import (
        PostTestCollector, EvidenceProfile,
    )
    collector = PostTestCollector(session_id="test", phase="praxic")
    monkeypatch.setattr(
        collector, "_get_session_changed_files",
        lambda: ["README.md", "notes.txt"],
    )
    monkeypatch.setattr(collector, "evidence_profile", "auto")

    profile = collector._resolve_profile()
    assert profile == EvidenceProfile.PROSE


def test_resolve_profile_returns_code_for_python_files(monkeypatch):
    """Python files trigger CODE profile (existing behavior, regression check)."""
    from empirica.core.post_test.collector import (
        PostTestCollector, EvidenceProfile,
    )
    collector = PostTestCollector(session_id="test", phase="praxic")
    monkeypatch.setattr(
        collector, "_get_session_changed_files",
        lambda: ["empirica/foo.py"],
    )
    monkeypatch.setattr(collector, "evidence_profile", "auto")

    profile = collector._resolve_profile()
    assert profile == EvidenceProfile.CODE
```

- [ ] **Step 2: Run tests — first should fail, others should pass-or-fail depending on current behavior**

```bash
pytest tests/core/post_test/test_collector_hardening.py -v -k "resolve_profile"
```

Expected: `test_resolve_profile_returns_insufficient_when_no_changed_files` FAILS (currently returns PROSE).

- [ ] **Step 3: Update `_resolve_profile()` AUTO branch**

In `empirica/core/post_test/collector.py:205-219`, replace the AUTO branch:

```python
        if profile == EvidenceProfile.AUTO:
            # Auto-detect from changed file extensions
            changed = self._get_session_changed_files()
            has_code = any(f.endswith('.py') for f in changed)
            from .web_collector import WEB_EXTENSIONS
            has_web = any(Path(f).suffix in WEB_EXTENSIONS for f in changed)
            if has_web and has_code:
                return EvidenceProfile.HYBRID
            elif has_web:
                return EvidenceProfile.WEB
            elif has_code:
                return EvidenceProfile.CODE
            elif changed:
                # Non-empty but neither code nor web → markdown, configs, data
                return EvidenceProfile.PROSE
            else:
                # Empty changed files → measurer has no file signal at all.
                # Out-of-repo work, remote-ops without explicit declaration,
                # or nothing-yet. Halt profile-specific collection; rely only
                # on universal collectors (artifacts, goals, etc.) and let the
                # coverage threshold gate decide whether calibration is meaningful.
                return EvidenceProfile.INSUFFICIENT
        return profile
```

- [ ] **Step 4: Run tests to verify all three pass**

```bash
pytest tests/core/post_test/test_collector_hardening.py -v -k "resolve_profile"
```

Expected: PASS for all three tests.

- [ ] **Step 5: Commit**

```bash
git add empirica/core/post_test/collector.py tests/core/post_test/test_collector_hardening.py
git commit -m "feat(collector): _resolve_profile returns INSUFFICIENT for empty changed files"
```

---

### Task 8: `collect_all` skips profile collectors when INSUFFICIENT

**Files:**
- Modify: `empirica/core/post_test/collector.py:355-417`
- Test: `tests/core/post_test/test_collector_hardening.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/core/post_test/test_collector_hardening.py — add
def test_insufficient_profile_skips_profile_collectors(monkeypatch):
    """When profile=INSUFFICIENT, no profile-specific collectors run."""
    from empirica.core.post_test.collector import (
        PostTestCollector, EvidenceProfile,
    )

    collector = PostTestCollector(session_id="test", phase="praxic")
    monkeypatch.setattr(collector, "_resolve_profile", lambda: EvidenceProfile.INSUFFICIENT)
    # Stub all collectors to return [] so we just observe which are invoked
    invoked = []
    for method in [
        "_collect_artifact_metrics", "_collect_goal_metrics",
        "_collect_issue_metrics", "_collect_triage_metrics",
        "_collect_codebase_model_metrics", "_collect_non_git_file_metrics",
        "_collect_test_results", "_collect_git_metrics",
        "_collect_code_quality_metrics",
    ]:
        monkeypatch.setattr(
            collector, method,
            lambda name=method: (invoked.append(name) or [])
        )

    bundle = collector.collect_all()

    # Universal praxic collectors should run; profile-specific (pytest, git,
    # code_quality) should NOT run when profile is INSUFFICIENT.
    assert "_collect_artifact_metrics" in invoked
    assert "_collect_goal_metrics" in invoked  # universal for praxic
    assert "_collect_test_results" not in invoked  # profile-specific (pytest)
    assert "_collect_git_metrics" not in invoked  # profile-specific
    assert "_collect_code_quality_metrics" not in invoked  # profile-specific
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/core/post_test/test_collector_hardening.py::test_insufficient_profile_skips_profile_collectors -v
```

Expected: FAIL — `_collect_test_results` IS in invoked (current code runs CODE profile collectors as fallback).

- [ ] **Step 3: Update collect_all to handle INSUFFICIENT**

In `empirica/core/post_test/collector.py`, modify the profile-collector branch (around line 392-416):

```python
        # Profile-specific collectors — only run during praxic/combined phases
        # AND when the profile actually has signal to grade. INSUFFICIENT and
        # noetic phase both skip profile collectors entirely.
        if self.phase == "noetic" or profile == EvidenceProfile.INSUFFICIENT:
            profile_collectors = []
        elif profile == EvidenceProfile.CODE:
            profile_collectors = [
                ("pytest", self._collect_test_results),
                ("git", self._collect_git_metrics),
                ("code_quality", self._collect_code_quality_metrics),
            ]
        elif profile == EvidenceProfile.PROSE:
            profile_collectors = self._get_prose_collectors()
        elif profile == EvidenceProfile.WEB:
            profile_collectors = self._get_web_collectors()
        elif profile == EvidenceProfile.HYBRID:
            profile_collectors = [
                ("pytest", self._collect_test_results),
                ("git", self._collect_git_metrics),
                ("code_quality", self._collect_code_quality_metrics),
            ] + self._get_prose_collectors() + self._get_web_collectors()
        else:
            # Fallback to code (preserves prior behavior for unknown profiles)
            profile_collectors = [
                ("pytest", self._collect_test_results),
                ("git", self._collect_git_metrics),
                ("code_quality", self._collect_code_quality_metrics),
            ]
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/core/post_test/test_collector_hardening.py::test_insufficient_profile_skips_profile_collectors -v
```

Expected: PASS.

- [ ] **Step 5: Run the full collector test file to catch regressions**

```bash
pytest tests/core/post_test/test_collector_hardening.py -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add empirica/core/post_test/collector.py tests/core/post_test/test_collector_hardening.py
git commit -m "feat(collector): skip profile collectors when profile is INSUFFICIENT"
```

---

## Phase 4: Source Error Capture

### Task 9: `collect_all` captures exception type+message and distinguishes empty from failed

**Files:**
- Modify: `empirica/core/post_test/collector.py:420-428`
- Test: `tests/core/post_test/test_collector_hardening.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/core/post_test/test_collector_hardening.py — add
def test_collect_all_captures_source_errors_with_type_and_message(monkeypatch):
    """When a collector raises, source_errors should capture type and message."""
    from empirica.core.post_test.collector import PostTestCollector

    collector = PostTestCollector(session_id="test", phase="praxic")

    def boom():
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(collector, "_collect_artifact_metrics", boom)
    # Stub others as no-ops
    for method in [
        "_collect_goal_metrics", "_collect_issue_metrics",
        "_collect_triage_metrics", "_collect_codebase_model_metrics",
        "_collect_non_git_file_metrics", "_resolve_profile",
    ]:
        monkeypatch.setattr(collector, method, lambda *a, **k: [] if method != "_resolve_profile" else "insufficient")

    bundle = collector.collect_all()

    assert "artifacts" in bundle.sources_failed
    assert "artifacts" in bundle.source_errors
    assert "RuntimeError" in bundle.source_errors["artifacts"]
    assert "simulated failure" in bundle.source_errors["artifacts"]


def test_collect_all_distinguishes_empty_from_failed(monkeypatch):
    """A collector returning [] goes to sources_empty; raising goes to sources_failed."""
    from empirica.core.post_test.collector import PostTestCollector

    collector = PostTestCollector(session_id="test", phase="praxic")

    monkeypatch.setattr(collector, "_collect_artifact_metrics", lambda: [])
    # Stub others
    monkeypatch.setattr(collector, "_resolve_profile", lambda: "insufficient")
    for method in [
        "_collect_goal_metrics", "_collect_issue_metrics",
        "_collect_triage_metrics", "_collect_codebase_model_metrics",
        "_collect_non_git_file_metrics",
    ]:
        monkeypatch.setattr(collector, method, lambda *a, **k: [])

    bundle = collector.collect_all()

    assert "artifacts" in bundle.sources_empty
    assert "artifacts" not in bundle.sources_failed
    assert "artifacts" not in bundle.sources_available
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/core/post_test/test_collector_hardening.py -v -k "source_errors or distinguishes_empty"
```

Expected: both FAIL.

- [ ] **Step 3: Update the collect_all exception handler**

In `empirica/core/post_test/collector.py` (around line 420-428):

```python
        for source_name, collector_fn in collectors:
            try:
                items = collector_fn()
                if items:
                    bundle.items.extend(items)
                    bundle.sources_available.append(source_name)
                else:
                    # Ran cleanly but found nothing to grade — different from
                    # crashing. Track separately so failures can be debugged.
                    bundle.sources_empty.append(source_name)
            except Exception as e:
                logger.debug(f"Evidence source {source_name} failed: {e}")
                bundle.sources_failed.append(source_name)
                # Capture type + truncated message for POSTFLIGHT surfacing.
                # Without this, sources_failed is opaque ("it failed but why?").
                err_type = type(e).__name__
                err_msg = str(e)[:200]
                bundle.source_errors[source_name] = f"{err_type}: {err_msg}"
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/core/post_test/test_collector_hardening.py -v -k "source_errors or distinguishes_empty"
```

Expected: PASS.

- [ ] **Step 5: Run the full collector test file to catch regressions**

```bash
pytest tests/core/post_test/test_collector_hardening.py -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add empirica/core/post_test/collector.py tests/core/post_test/test_collector_hardening.py
git commit -m "feat(collector): capture source_errors and distinguish empty from failed"
```

---

## Phase 5: Coverage Threshold + Status Gate

### Task 10: `INSUFFICIENT_EVIDENCE_THRESHOLD` constant + threshold gate in `_run_single_phase_verification`

**Files:**
- Modify: `empirica/core/post_test/grounded_calibration.py` (around line 595-676)
- Test: `tests/core/post_test/test_grounded_calibration_threshold.py`

The threshold constant lives at module level so it's easy to promote to project.yaml later (deferred item #6). When coverage < threshold, return a special-status response without computing meaningful gaps.

- [ ] **Step 1: Write the failing test**

```python
# tests/core/post_test/test_grounded_calibration_threshold.py — NEW FILE
"""Tests for the coverage-threshold gate in _run_single_phase_verification.

Strategy: monkeypatch PostTestCollector.collect_all to return controlled
EvidenceBundle objects instead of building a real session DB. This isolates
the threshold-gate logic from the collector's dependency surface.
"""

import pytest
from empirica.core.post_test.collector import (
    EvidenceBundle, EvidenceItem, EvidenceQuality, PostTestCollector,
)
from empirica.core.post_test.grounded_calibration import (
    _run_single_phase_verification, INSUFFICIENT_EVIDENCE_THRESHOLD,
)


def _make_bundle_with_n_vectors(n: int) -> EvidenceBundle:
    """Build an EvidenceBundle whose items together touch exactly n distinct vectors."""
    bundle = EvidenceBundle(session_id="threshold-test")
    vector_names = ["know", "signal", "context", "do", "change", "state",
                    "clarity", "coherence", "density", "completion", "impact",
                    "engagement"]
    for i in range(n):
        bundle.items.append(EvidenceItem(
            source="artifacts",
            metric_name=f"metric_{i}",
            value=0.7,
            raw_value={"count": 1},
            quality=EvidenceQuality.OBJECTIVE,
            supports_vectors=[vector_names[i]],
        ))
    bundle.sources_available = ["artifacts"]
    bundle.coverage = n / 13.0
    return bundle


def test_coverage_below_threshold_returns_insufficient_status(monkeypatch):
    """When grounded_coverage < INSUFFICIENT_EVIDENCE_THRESHOLD, response
    should have calibration_status=insufficient_evidence and empty gaps."""
    # 1 vector grounded out of 13 → coverage ≈ 0.077, well below 0.3
    bundle = _make_bundle_with_n_vectors(1)
    monkeypatch.setattr(
        PostTestCollector, "collect_all", lambda self: bundle
    )

    result = _run_single_phase_verification(
        session_id="threshold-test",
        vectors={"know": 0.7, "uncertainty": 0.3},
        db=None,  # mocked collector means DB unused
        phase="praxic",
        work_type="code",
    )

    assert result is not None
    assert result["calibration_status"] == "insufficient_evidence"
    assert result["gaps"] == {}
    assert result["holistic_calibration_score"] is None
    assert result["grounded_coverage"] < INSUFFICIENT_EVIDENCE_THRESHOLD
    assert "self_assessed" in result
    assert result["self_assessed"]["know"] == 0.7
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/core/post_test/test_grounded_calibration_threshold.py::test_coverage_below_threshold_returns_insufficient_status -v
```

Expected: FAIL with `KeyError: 'calibration_status'` or `ImportError` for the constant.

- [ ] **Step 3: Add constant + helper function + threshold gate (also fix early-return collision)**

In `empirica/core/post_test/grounded_calibration.py`, add near the top of the module (after imports):

```python
# Below this grounded coverage, calibration is statistically meaningless —
# halt gap computation rather than emit phantom scores. The AI's
# self-assessment stands. Promotable to project.yaml later (deferred
# work item — see spec doc).
INSUFFICIENT_EVIDENCE_THRESHOLD = 0.3


def _build_insufficient_evidence_response(
    phase: str,
    vectors: dict,
    bundle,  # may be None for the "no collection at all" case
    grounded_coverage: float,
    reason: str,
) -> dict:
    """Build the calibration response for insufficient-evidence cases.

    Used by:
    1. The empty-bundle early return (collector returned no items)
    2. The threshold gate (coverage < INSUFFICIENT_EVIDENCE_THRESHOLD)
    Both produce the same response shape so callers see one consistent
    'self-assessment stands' format.
    """
    return {
        'verification_id': None,
        'phase': phase,
        'evidence_count': len(bundle.items) if bundle else 0,
        'sources': bundle.sources_available if bundle else [],
        'sources_failed': bundle.sources_failed if bundle else [],
        'sources_empty': getattr(bundle, 'sources_empty', []) if bundle else [],
        'source_errors': getattr(bundle, 'source_errors', {}) if bundle else {},
        'grounded_coverage': round(grounded_coverage, 2),
        'calibration_score': None,
        'holistic_calibration_score': None,
        'calibration_status': 'insufficient_evidence',
        'reason': reason,
        'gaps': {},
        'updates': {},
        'insufficient_evidence_vectors': sorted(vectors.keys()),
        'self_assessed': dict(vectors),
        'note': (
            'Insufficient grounded evidence to compute calibration. '
            'Self-assessment stands.'
        ),
    }
```

Now modify `_run_single_phase_verification`. **CRITICAL:** the existing function has an early return at line ~624 (`if not bundle.items: return None`) that will preempt the threshold gate. Replace it with a call to the helper, then add the threshold gate after `mapper.map_evidence()`:

```python
    bundle = collector.collect_all()

    # CHANGED: empty bundle is no longer a silent None return.
    # It's an insufficient-evidence case — surface it through the helper.
    if not bundle.items:
        logger.debug(f"No {phase} evidence collected, returning insufficient_evidence")
        return _build_insufficient_evidence_response(
            phase=phase,
            vectors=vectors,
            bundle=bundle,
            grounded_coverage=0.0,
            reason="no evidence items collected (out-of-repo work or empty session)",
        )

    mapper = EvidenceMapper()
    assessment = mapper.map_evidence(
        bundle, vectors, phase=phase, domain=domain or "default",
        per_vector_weights=per_vector_weights,
        work_type=work_type,
    )

    # NEW: Coverage threshold gate. If grounded_coverage is below the threshold,
    # the bundle had items but they didn't ground enough vectors to produce
    # statistically meaningful calibration. Halt and surface as insufficient.
    if assessment.grounded_coverage < INSUFFICIENT_EVIDENCE_THRESHOLD:
        return _build_insufficient_evidence_response(
            phase=phase,
            vectors=vectors,
            bundle=bundle,
            grounded_coverage=assessment.grounded_coverage,
            reason=(
                f"grounded_coverage {assessment.grounded_coverage:.2f} < "
                f"threshold {INSUFFICIENT_EVIDENCE_THRESHOLD}"
            ),
        )

    # Normal grounded path continues unchanged below
    manager = GroundedCalibrationManager(db)
    # ... rest of existing function
```

Also add `'calibration_status': 'grounded'` to the existing return dict at the bottom of `_run_single_phase_verification` (around line 651-676) for status consistency:

```python
    return {
        'verification_id': verification_id,
        'phase': phase,
        # ... existing fields ...
        'calibration_status': 'grounded',  # NEW: explicit status for the happy path
    }
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/core/post_test/test_grounded_calibration_threshold.py::test_coverage_below_threshold_returns_insufficient_status -v
```

Expected: PASS.

- [ ] **Step 5: Add the inverse test (append to same file)**

```python
def test_coverage_above_threshold_returns_grounded_status(monkeypatch, tmp_path):
    """When grounded_coverage >= INSUFFICIENT_EVIDENCE_THRESHOLD, normal
    grounded calibration response."""
    # 5 vectors grounded out of 13 → coverage ≈ 0.385, above 0.3
    bundle = _make_bundle_with_n_vectors(5)
    monkeypatch.setattr(
        PostTestCollector, "collect_all", lambda self: bundle
    )

    # The grounded path will try to use a real DB for storage. Use a temp
    # SQLite DB that follows the existing test infrastructure pattern; if
    # the existing tests use a `db_fixture` or similar, reuse it. Falling
    # back to a minimal stand-in:
    from empirica.data.session_database import SessionDatabase
    db = SessionDatabase(db_path=str(tmp_path / "threshold.db"))

    result = _run_single_phase_verification(
        session_id="threshold-test-grounded",
        vectors={"know": 0.7, "uncertainty": 0.3},
        db=db,
        phase="praxic",
        work_type="code",
    )

    assert result["calibration_status"] == "grounded"
    assert result["grounded_coverage"] >= INSUFFICIENT_EVIDENCE_THRESHOLD
    assert "gaps" in result


def test_empty_bundle_returns_insufficient_status(monkeypatch):
    """A collected-but-empty bundle (no items) should return insufficient_evidence,
    not None — verifying the early-return collision fix."""
    empty = EvidenceBundle(session_id="empty-test")
    monkeypatch.setattr(
        PostTestCollector, "collect_all", lambda self: empty
    )

    result = _run_single_phase_verification(
        session_id="empty-test",
        vectors={"know": 0.7, "uncertainty": 0.3},
        db=None,
        phase="praxic",
        work_type="code",
    )

    assert result is not None  # was returning None before the fix
    assert result["calibration_status"] == "insufficient_evidence"
    assert result["grounded_coverage"] == 0.0
```

- [ ] **Step 6: Run both tests**

```bash
pytest tests/core/post_test/test_grounded_calibration_threshold.py -v -k "threshold"
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add empirica/core/post_test/grounded_calibration.py tests/core/post_test/test_grounded_calibration_threshold.py
git commit -m "feat(calibration): add INSUFFICIENT_EVIDENCE_THRESHOLD gate"
```

---

### Task 11: `remote-ops` short-circuit in `_run_single_phase_verification`

**Files:**
- Modify: `empirica/core/post_test/grounded_calibration.py` (top of `_run_single_phase_verification`)
- Test: `tests/core/post_test/test_remote_ops.py`

The `remote-ops` path is functionally identical to insufficient_evidence (skip the trajectory write, return self-assessment-stands), but with a distinct status string so users see WHY their calibration was skipped (declared vs measured).

- [ ] **Step 1: Write the failing test**

```python
# tests/core/post_test/test_remote_ops.py — add
def test_run_single_phase_verification_remote_ops_returns_ungrounded_status(tmp_path):
    """work_type=remote-ops should bypass collection entirely and return
    calibration_status=ungrounded_remote_ops."""
    from empirica.core.post_test.grounded_calibration import _run_single_phase_verification

    result = _run_single_phase_verification(
        session_id="rops-test",
        vectors={"know": 0.8, "uncertainty": 0.2},
        db=None,  # remote-ops should not need DB access
        phase="praxic",
        work_type="remote-ops",
    )
    assert result is not None
    assert result["calibration_status"] == "ungrounded_remote_ops"
    assert result["holistic_calibration_score"] is None
    assert result["gaps"] == {}
    assert result["self_assessed"] == {"know": 0.8, "uncertainty": 0.2}
    # Every passed vector is flagged as insufficient_evidence
    assert set(result["insufficient_evidence_vectors"]) == {"know", "uncertainty"}
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/core/post_test/test_remote_ops.py::test_run_single_phase_verification_remote_ops_returns_ungrounded_status -v
```

Expected: FAIL — current code tries to use the DB even for remote-ops.

- [ ] **Step 3: Add the short-circuit at the top of `_run_single_phase_verification`**

In `empirica/core/post_test/grounded_calibration.py`, add at the very start of `_run_single_phase_verification` (before the `PostTestCollector` instantiation):

```python
def _run_single_phase_verification(
    session_id: str,
    vectors: dict[str, float],
    db,
    phase: str,
    project_id: str | None = None,
    domain: str | None = None,
    goal_id: str | None = None,
    check_timestamp: float | None = None,
    evidence_profile: str | None = None,
    work_context: str | None = None,
    work_type: str | None = None,
    preflight_timestamp: float | None = None,
    per_vector_weights: dict[str, float] | None = None,
    transaction_id: str | None = None,
) -> dict | None:
    """Run grounded verification for a single phase (noetic, praxic, or combined)."""

    # Remote-ops short-circuit: by declaration, the local Sentinel has no
    # signal for this work. Skip collection entirely, return self-assessment.
    # Future: a RemoteVerifier on target machines posting EvidenceItem[]
    # back via the dispatch bus will populate this path with real data.
    if work_type == "remote-ops":
        return {
            'verification_id': None,
            'phase': phase,
            'evidence_count': 0,
            'sources': [],
            'sources_failed': [],
            'sources_empty': [],
            'source_errors': {},
            'grounded_coverage': 0.0,
            'calibration_score': None,
            'holistic_calibration_score': None,
            'calibration_status': 'ungrounded_remote_ops',
            'reason': (
                'work_type=remote-ops: local Sentinel has no signal '
                'for this work by declaration'
            ),
            'gaps': {},
            'updates': {},
            'insufficient_evidence_vectors': sorted(vectors.keys()),
            'self_assessed': dict(vectors),
            'note': 'Remote work by declaration. Self-assessment stands.',
        }

    # Existing path continues unchanged
    collector = PostTestCollector(...)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/core/post_test/test_remote_ops.py::test_run_single_phase_verification_remote_ops_returns_ungrounded_status -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add empirica/core/post_test/grounded_calibration.py tests/core/post_test/test_remote_ops.py
git commit -m "feat(calibration): short-circuit remote-ops in _run_single_phase_verification"
```

---

## Phase 6: Integration Glue — DROPPED via T0 Discovery

> **2026-04-08 update:** T0 discovery (`docs/superpowers/plans/2026-04-08-discovery-notes.md`) verified that `store_verification` and `record_trajectory_point` each have exactly ONE call site, both AFTER the early-return point in `_run_single_phase_verification`. The Task 10 threshold gate and Task 11 remote-ops short-circuit naturally prevent non-grounded transactions from reaching ANY storage table. Tasks 12, 13, and 14 below are therefore no-ops and have been DROPPED. The architectural property is verified by `test_empty_bundle_returns_insufficient_status` (Task 10 Step 5) and the integration test (Task 16). Skip directly to **Phase 7: Documentation Surface** below.

### ~~Task 12: Trajectory tracker skips non-grounded transactions~~ — DROPPED

**Files:**
- Modify: `empirica/core/post_test/trajectory_tracker.py` (find `record_trajectory_point`)
- Test: `tests/core/post_test/test_grounded_calibration_threshold.py` OR new trajectory test

- [ ] **Step 1: Locate the trajectory write site**

```bash
grep -n "record_trajectory_point\|def record" empirica/core/post_test/trajectory_tracker.py
```

Record the function signature.

- [ ] **Step 2: Write the failing test**

```python
# tests/core/post_test/test_grounded_calibration_threshold.py — add
def test_insufficient_evidence_does_not_write_trajectory(tmp_path):
    """A transaction with calibration_status != 'grounded' should not write
    a trajectory point."""
    from empirica.core.post_test.trajectory_tracker import TrajectoryTracker
    from empirica.core.post_test.mapper import GroundedAssessment

    db = build_test_db(tmp_path)
    assessment = GroundedAssessment(
        session_id="test",
        self_assessed={"know": 0.7},
        grounded={},
        calibration_gaps={},
        grounded_coverage=0.1,
        overall_calibration_score=0.0,
        calibration_status="insufficient_evidence",
    )

    tracker = TrajectoryTracker(db)
    initial_count = count_trajectory_points(db)
    tracker.record_trajectory_point("test", assessment, phase="praxic")
    final_count = count_trajectory_points(db)

    assert final_count == initial_count, "trajectory should not be written for insufficient_evidence"


def test_remote_ops_does_not_write_trajectory(tmp_path):
    """Same for ungrounded_remote_ops."""
    # Same setup as above but with calibration_status="ungrounded_remote_ops"
    # ... assert no trajectory write
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
pytest tests/core/post_test/test_grounded_calibration_threshold.py -v -k "trajectory"
```

Expected: FAIL — current `record_trajectory_point` writes regardless of status.

- [ ] **Step 4: Add the guard to `record_trajectory_point`**

In `empirica/core/post_test/trajectory_tracker.py`, at the start of `record_trajectory_point`:

```python
def record_trajectory_point(self, session_id, assessment, ...):
    """Record a trajectory drift point.

    Skips non-grounded calibrations: insufficient_evidence and
    ungrounded_remote_ops are observation-skipping events. Writing them
    would poison the trajectory with noise.
    """
    status = getattr(assessment, "calibration_status", "grounded")
    if status != "grounded":
        logger.debug(
            f"Skipping trajectory write for session {session_id}: "
            f"calibration_status={status}"
        )
        return

    # Existing trajectory write logic continues unchanged
    ...
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
pytest tests/core/post_test/test_grounded_calibration_threshold.py -v -k "trajectory"
```

Expected: PASS for both new tests; existing trajectory tests still pass.

- [ ] **Step 6: Commit**

```bash
git add empirica/core/post_test/trajectory_tracker.py tests/core/post_test/test_grounded_calibration_threshold.py
git commit -m "feat(trajectory): skip writes for non-grounded calibrations"
```

---

### ~~Task 13: `previous_transaction_feedback` aggregation excludes non-grounded~~ — DROPPED

**Files:**
- Modify: file identified in Task 0 Step 1 (likely `workflow_commands.py` or `trajectory_tracker.py`)
- Test: in same module's test file

- [ ] **Step 1: Open the identified file (from Task 0 Step 1) and find the aggregation function**

Look for code that builds the dict with keys `overestimate_tendency`, `underestimate_tendency`, `calibration_score`, `grounded_coverage` shown at PREFLIGHT response. This is the function that reads recent trajectory rows and computes directional advice for the AI.

- [ ] **Step 2: Write the failing test (in the test file matching the production module)**

If the production code lives in `empirica/cli/command_handlers/workflow_commands.py`, add the test to `tests/unit/cli/test_workflow_commands_delta_fix.py` (existing). If it lives elsewhere, create a sibling test file matching the existing test naming convention.

```python
# Test sketch — adapt to the actual function name and DB fixture
def test_previous_transaction_feedback_excludes_non_grounded(tmp_path):
    """Trajectory rows with calibration_status != 'grounded' should NOT
    contribute to overestimate_tendency / underestimate_tendency aggregation."""
    import sqlite3
    db_path = tmp_path / "feedback.db"
    # Use the existing test DB setup pattern from test_workflow_commands_delta_fix.py
    setup_test_session_db(db_path)

    # Insert 3 trajectory rows: 2 grounded with consistent overestimate on
    # `know`, 1 ungrounded_remote_ops with extreme underestimate.
    insert_trajectory_row(db_path, calibration_status="grounded",
                          gaps={"know": 0.3})  # AI overestimates know
    insert_trajectory_row(db_path, calibration_status="grounded",
                          gaps={"know": 0.25})  # consistent
    insert_trajectory_row(db_path, calibration_status="ungrounded_remote_ops",
                          gaps={"know": -0.5})  # SHOULD BE IGNORED

    # Call the aggregation function (name TBD from Task 0 Step 1)
    feedback = build_previous_transaction_feedback(db_path, session_id="...")

    # The remote-ops row should not have flipped the tendency
    assert "know" in feedback["overestimate_tendency"]
    assert "know" not in feedback.get("underestimate_tendency", [])
```

- [ ] **Step 3: Run test to verify it fails**

```bash
pytest tests/unit/cli/test_workflow_commands_delta_fix.py::test_previous_transaction_feedback_excludes_non_grounded -v
```

(Substitute the actual test path if different.)

- [ ] **Step 4: Add a `WHERE calibration_status = 'grounded'` filter**

If trajectory rows already store `calibration_status` (verified in Task 0 Step 4), add it directly to the SELECT query. If not, follow the read-time-default decision from Task 0 Step 4 — rows without status assume `"grounded"` (legacy behavior preserved):

```python
# Example SQL change — adapt to the actual query string
cursor.execute("""
    SELECT vector_name, gap_value
    FROM calibration_trajectory
    WHERE session_id IN (...)
      AND COALESCE(calibration_status, 'grounded') = 'grounded'  -- NEW
    ORDER BY timestamp DESC LIMIT ?
""", params)
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
pytest tests/unit/cli/test_workflow_commands_delta_fix.py -v
```

Expected: new test passes, existing tests still pass.

- [ ] **Step 6: Commit**

```bash
git add empirica/cli/command_handlers/workflow_commands.py \
        tests/unit/cli/test_workflow_commands_delta_fix.py
git commit -m "feat(feedback): exclude non-grounded transactions from previous_transaction_feedback"
```

(Adjust file paths if Task 0 Step 1 identified a different module.)

---

### ~~Task 14: Sentinel gate handles new calibration_status values~~ — DROPPED

**Files:**
- Modify: `empirica/plugins/claude-code-integration/hooks/sentinel-gate.py` (line numbers from Task 0 Step 2)
- Test: there may not be hook tests; if not, an integration test in tests/plugins/

The gate should: when reading metacog/calibration data, if `calibration_status != "grounded"`, treat it as `decision="proceed"` and skip metacog updates. Don't block.

- [ ] **Step 1: Open `sentinel-gate.py` and locate the metacog read site**

(Use the line numbers recorded in Task 0 Step 2.)

- [ ] **Step 2: Write the failing test (if test infrastructure exists)**

If `tests/plugins/test_sentinel_gate.py` or similar exists, add a test that POSTFLIGHT response with `calibration_status="ungrounded_remote_ops"` results in gate decision "proceed" without metacog state changes.

- [ ] **Step 3: Add the guard in sentinel-gate.py**

Pseudocode:

```python
calibration = postflight_response.get("calibration", {})
status = calibration.get("calibration_status", "grounded")

if status != "grounded":
    # Non-grounded transactions don't update metacog; gate decision is
    # "proceed" with an advisory note. Don't block remote-ops or
    # insufficient-evidence work.
    return {"decision": "proceed", "note": calibration.get("note", "")}

# Existing grounded calibration handling continues
```

- [ ] **Step 4: Run tests to verify they pass**

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(sentinel-gate): handle non-grounded calibration_status values"
```

---

## Phase 7: Documentation Surface

### Task 15: Update work_type enum docs across system prompt + skills

**Files:**
- Modify: `empirica/plugins/claude-code-integration/templates/CLAUDE.md`
- Modify: `empirica/plugins/claude-code-integration/skills/epistemic-transaction/SKILL.md`
- Modify: `empirica/plugins/claude-code-integration/skills/empirica-constitution/SKILL.md`

These are all doc-only updates. No tests, single commit at the end.

- [ ] **Step 1: Update CLAUDE.md template**

Find the section listing work_type values (search for `code|infra|research|release`). Add `remote-ops` to the list with this one-line definition:

```
- remote-ops: work on a machine the local Sentinel doesn't observe (SSH, customer machines, remote config, on-site assistance). Self-assessment stands.
```

- [ ] **Step 2: Update epistemic-transaction skill SKILL.md**

Find the work_type list (same pattern). Add:

```markdown
- **remote-ops** — Work on a machine the local Sentinel doesn't observe (SSH sessions, customer machines, remote config, deploys without local commits, on-site assistance). The local measurer has no signal — self-assessment stands. Future: RemoteVerifier agents posting back via dispatch bus.
```

- [ ] **Step 3: Update empirica-constitution skill with routing rule**

Find the routing decision tree section. Add a rule near the work_type guidance:

```markdown
**Routing rule: declare work_type=remote-ops when**
- Your work happens on a machine you don't have local Sentinel coverage for (SSH sessions, customer/partner machines, remote config edits)
- You're doing on-site assistance or onboarding for an external contact
- Local git won't see the changes you're about to make

Don't use remote-ops for hybrid work that ALSO touches local code — split into two transactions instead.
```

- [ ] **Step 4: Verify the changes look right**

```bash
grep -rn "remote-ops" empirica/plugins/claude-code-integration/
```

Expected: 3+ matches (one per file).

- [ ] **Step 5: Commit**

```bash
git add empirica/plugins/claude-code-integration/templates/CLAUDE.md \
        empirica/plugins/claude-code-integration/skills/epistemic-transaction/SKILL.md \
        empirica/plugins/claude-code-integration/skills/empirica-constitution/SKILL.md
git commit -m "docs(work_type): document remote-ops in system prompt and skills"
```

---

## Phase 8: Integration Test

### Task 16: End-to-end POSTFLIGHT cycle with `remote-ops`

**Files:**
- Test: `tests/core/post_test/test_remote_ops.py`

- [ ] **Step 1: Write end-to-end integration test (shells out to the real CLI)**

```python
# tests/core/post_test/test_remote_ops.py — APPEND
import json
import subprocess
import os


def _empirica(args: list[str], stdin_json: dict | None = None,
              env: dict | None = None) -> dict:
    """Run an empirica CLI command and return the parsed JSON response."""
    full_env = {**os.environ, **(env or {})}
    proc = subprocess.run(
        ["empirica", *args, "--output", "json"],
        input=json.dumps(stdin_json) if stdin_json else None,
        capture_output=True,
        text=True,
        env=full_env,
        timeout=30,
    )
    assert proc.returncode == 0, f"empirica {args} failed: {proc.stderr}"
    # Some commands print non-JSON preamble; find the JSON object in stdout
    out = proc.stdout.strip()
    return json.loads(out.split("\n")[-1]) if out else {}


def test_full_postflight_cycle_with_remote_ops(tmp_path, monkeypatch):
    """PREFLIGHT → CHECK → POSTFLIGHT all with work_type=remote-ops should:
    - Validate the work_type at PREFLIGHT
    - Return calibration_status=ungrounded_remote_ops at CHECK and POSTFLIGHT
    - Not write to learning_trajectory
    - Display self-assessed vectors back to the user unchanged
    """
    # Isolate to a temp session DB by setting the empirica DB path env var.
    # (Adjust env var name to match the project's actual override mechanism;
    # if no env var exists, use the existing test fixture pattern.)
    monkeypatch.setenv("EMPIRICA_DB_PATH", str(tmp_path / "test.db"))

    # 1. Create a session
    session_resp = _empirica(["session-create", "--ai-id", "claude-code"])
    session_id = session_resp["session_id"]

    # 2. PREFLIGHT with work_type=remote-ops
    preflight_resp = _empirica(
        ["preflight-submit", "-"],
        stdin_json={
            "session_id": session_id,
            "vectors": {"know": 0.7, "uncertainty": 0.3},
            "work_type": "remote-ops",
            "intent": "remote-ops integration test",
        },
    )
    assert preflight_resp["ok"]

    # 3. CHECK
    check_resp = _empirica(
        ["check-submit", "-"],
        stdin_json={
            "session_id": session_id,
            "vectors": {"know": 0.75, "uncertainty": 0.25},
            "check_summary": "remote-ops integration check",
        },
    )
    assert check_resp["ok"]
    # CHECK may or may not run calibration depending on implementation; if it
    # does, status must be ungrounded_remote_ops:
    if "calibration" in check_resp:
        assert check_resp["calibration"].get("calibration_status") == \
            "ungrounded_remote_ops"

    # 4. POSTFLIGHT
    postflight_resp = _empirica(
        ["postflight-submit", "-"],
        stdin_json={
            "session_id": session_id,
            "vectors": {"know": 0.8, "uncertainty": 0.2},
            "summary": "remote-ops integration test complete",
        },
    )
    assert postflight_resp["ok"]
    cal = postflight_resp["calibration"]
    assert cal["calibration_status"] == "ungrounded_remote_ops"
    assert cal["holistic_calibration_score"] is None
    assert cal["gaps"] == {}

    # 5. Trajectory should not have a new entry for this session
    # (read from the temp DB and assert no row was written)
    import sqlite3
    conn = sqlite3.connect(tmp_path / "test.db")
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM calibration_trajectory WHERE session_id = ?",
            (session_id,),
        ).fetchone()[0]
        assert count == 0, "remote-ops should not write to trajectory"
    finally:
        conn.close()
```

> **Note for the implementer:** if the `EMPIRICA_DB_PATH` env var doesn't exist, replace the `monkeypatch.setenv` line with whatever DB-isolation mechanism the existing integration tests in `tests/` use (likely a fixture that builds a temp `.empirica/sessions/sessions.db`). The key is: this test must run against an isolated DB, not the user's real one.

- [ ] **Step 2: Run the integration test**

```bash
pytest tests/core/post_test/test_remote_ops.py -v -k "full_postflight"
```

Expected: PASS.

- [ ] **Step 3: Run the entire post_test test suite as a regression check**

```bash
pytest tests/core/post_test/ -v
```

Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
git add tests/core/post_test/test_remote_ops.py
git commit -m "test(remote-ops): end-to-end PREFLIGHT/CHECK/POSTFLIGHT integration test"
```

---

## Final Validation

### Task 17: Manual smoke test

After all tasks pass, run a manual smoke test through the actual CLI to verify the user-facing experience:

- [ ] **Step 1: Run a remote-ops PREFLIGHT/CHECK/POSTFLIGHT cycle**

```bash
cd /home/yogapad/empirical-ai/empirica
echo '{"vectors":{"know":0.8,"uncertainty":0.2},"work_type":"remote-ops","intent":"smoke test"}' | empirica preflight-submit -
echo '{"vectors":{"know":0.85,"uncertainty":0.2},"check_summary":"smoke test"}' | empirica check-submit -
echo '{"vectors":{"know":0.85,"uncertainty":0.15},"summary":"smoke test"}' | empirica postflight-submit -
```

Expected: each response includes `calibration_status: "ungrounded_remote_ops"`, no calibration_gaps, holistic_calibration_score is null, no errors.

- [ ] **Step 2: Verify a normal code transaction still works**

```bash
echo '{"vectors":{"know":0.8,"uncertainty":0.2},"work_type":"code","intent":"normal smoke test"}' | empirica preflight-submit -
# ... CHECK and POSTFLIGHT
```

Expected: normal `calibration_status: "grounded"` response with real gaps.

- [ ] **Step 3: Verify out-of-repo work falls into insufficient_evidence**

In a directory with no .py changes (e.g., editing a markdown file outside the repo), run preflight without work_type:

```bash
echo '{"vectors":{"know":0.7,"uncertainty":0.3},"intent":"out of repo test"}' | empirica preflight-submit -
# CHECK then POSTFLIGHT
```

Expected: `calibration_status: "insufficient_evidence"` with low coverage.

---

## Notes for the executor

- **Worktree**: This plan should run in a fresh worktree on a topic branch. If brainstorming didn't create one, create it now: `git worktree add ../empirica-remote-ops -b feat/remote-ops-work-type develop`.
- **Pre-task Discovery**: Task 0 is mandatory. Don't skip — its outputs are inputs to Tasks 13-14.
- **Existing test infrastructure**: many tests reference `_get_db()`, `db.conn.cursor()`, fixtures that build a SessionDatabase. Reuse those patterns instead of inventing new fixtures.
- **TDD discipline**: write the test FIRST, run it to see it fail, then implement, then run to see it pass. Don't skip the failure step — it confirms the test actually exercises the change.
- **Frequent commits**: every task ends with a commit. The full plan should produce ~16 commits, each independently revertable.
- **If a test fixture is hard to build**: prefer monkeypatch over real DB setup for unit tests; reserve real DB only for the integration test in Task 16.
- **Don't refactor unrelated code**: stay focused. The spec is intentionally narrow.
- **Reviewer recommendations from spec review**: items #1-#5 from the spec are addressed in Task 0 Discovery. Items #6-#8 are explicitly deferred (see spec doc).
