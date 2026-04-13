"""
Grounded Calibration Manager

Parallel Bayesian track that uses objective evidence as observations
instead of self-assessed POSTFLIGHT vectors.

Mirrors BayesianBeliefManager but with:
- Lower observation variance (0.05 vs 0.1) — higher trust in objective evidence
- Evidence from PostTestCollector/EvidenceMapper instead of self-assessment
- Tracks divergence between self-referential and grounded tracks
- Stores in grounded_beliefs table (parallel to bayesian_beliefs)

The key insight: the existing calibration measures learning (PREFLIGHT→POSTFLIGHT delta),
not calibration accuracy. This track measures how well POSTFLIGHT self-assessment
matches what actually happened (objective evidence).

IMPORTANT — Dual-Track Calibration Philosophy:

Track 2 (grounded) is INFORMATIVE, not AUTHORITATIVE. The deterministic evidence
sources (test results, git metrics, artifact counts, code quality) are proxies —
useful signals that detect drift patterns, but they cannot fully measure holistic
epistemic state. An AI's self-assessment captures dimensions (understanding depth,
conceptual clarity, engagement quality) that no deterministic service can observe.

The two tracks are complementary:
- Track 1 (self-referential): Measures learning trajectory. The AI knows what it
  learned, but may have systematic biases in self-reporting.
- Track 2 (grounded): Detects those biases by comparing self-reports against
  observable outcomes. But the observables are incomplete — they're a flashlight
  on a few corners of the epistemic room, not a full map.

When the tracks diverge, that's a signal worth investigating — not an automatic
override. The AI should examine WHY they diverge and calibrate accordingly, not
blindly chase grounded scores by deflating vectors.

The holistic_calibration_score is a drift indicator, not a grade.
"""

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from .collector import EvidenceBundle, PostTestCollector
from .mapper import (
    UNGROUNDABLE_VECTORS,
    EvidenceMapper,
    GroundedAssessment,
)

logger = logging.getLogger(__name__)


# Below this grounded coverage, calibration is statistically meaningless —
# halt gap computation rather than emit phantom scores from sparse data.
# The AI's self-assessment stands. Promotable to project.yaml later (deferred
# work item — see docs/superpowers/specs/2026-04-08-sentinel-measurer-remote-ops-design.md).
INSUFFICIENT_EVIDENCE_THRESHOLD = 0.3


def _build_insufficient_evidence_response(
    phase: str,
    vectors: dict,
    bundle: Optional[EvidenceBundle],
    grounded_coverage: float,
    reason: str,
    status: str = "insufficient_evidence",
    note: str = (
        "Insufficient grounded evidence to compute calibration. "
        "Self-assessment stands."
    ),
) -> dict:
    """Build the calibration response for insufficient-evidence cases.

    Used by:
    1. The remote-ops short-circuit (status='ungrounded_remote_ops')
    2. The empty-bundle early return (collector returned no items)
    3. The threshold gate (coverage < INSUFFICIENT_EVIDENCE_THRESHOLD)

    All three produce the same response shape so callers see one consistent
    'self-assessment stands' format. None of them write to grounded_verifications
    or calibration_trajectory because this function returns BEFORE the storage
    operations in _run_single_phase_verification.
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
        'calibration_status': status,
        'reason': reason,
        'gaps': {},
        'updates': {},
        'insufficient_evidence_vectors': sorted(vectors.keys()),
        'self_assessed': dict(vectors),
        'note': note,
    }


@dataclass
class GroundedBelief:
    """A Bayesian belief grounded in objective evidence."""
    vector_name: str
    mean: float
    variance: float
    evidence_count: int
    last_observation: float
    last_observation_source: str
    self_referential_mean: float | None
    divergence: float | None
    last_updated: float


class GroundedCalibrationManager:
    """
    Manages grounded calibration beliefs using objective evidence.

    Parallel to BayesianBeliefManager, but observations come from
    deterministic sources (test results, git metrics, artifact counts)
    instead of self-assessment.
    """

    DEFAULT_PRIOR_MEAN = 0.5
    DEFAULT_PRIOR_VARIANCE = 0.25

    # Lower than self-referential (0.1) — we trust objective evidence more
    OBSERVATION_VARIANCE = 0.05

    TRACKED_VECTORS = [
        'engagement', 'know', 'do', 'context',
        'clarity', 'coherence', 'signal', 'density',
        'state', 'change', 'completion', 'impact', 'uncertainty'
    ]

    def __init__(self, db):
        self.db = db
        self.conn = db.conn

    def get_grounded_beliefs(self, ai_id: str) -> dict[str, GroundedBelief]:
        """Get current grounded beliefs for an AI, most recent per vector."""
        cursor = self.conn.cursor()

        cursor.execute("""
            SELECT vector_name, mean, variance, evidence_count,
                   last_observation, last_observation_source,
                   self_referential_mean, divergence, last_updated
            FROM grounded_beliefs
            WHERE ai_id = ?
            ORDER BY last_updated DESC
        """, (ai_id,))

        beliefs = {}
        seen = set()

        for row in cursor.fetchall():
            vector_name = row[0]
            if vector_name not in seen:
                beliefs[vector_name] = GroundedBelief(
                    vector_name=vector_name,
                    mean=row[1],
                    variance=row[2],
                    evidence_count=row[3],
                    last_observation=row[4],
                    last_observation_source=row[5],
                    self_referential_mean=row[6],
                    divergence=row[7],
                    last_updated=row[8],
                )
                seen.add(vector_name)

        # Fill defaults for missing groundable vectors
        for vector in self.TRACKED_VECTORS:
            if vector not in beliefs and vector not in UNGROUNDABLE_VECTORS:
                beliefs[vector] = GroundedBelief(
                    vector_name=vector,
                    mean=self.DEFAULT_PRIOR_MEAN,
                    variance=self.DEFAULT_PRIOR_VARIANCE,
                    evidence_count=0,
                    last_observation=0.0,
                    last_observation_source="none",
                    self_referential_mean=None,
                    divergence=None,
                    last_updated=0.0,
                )

        return beliefs

    def _get_disputed_vectors(self) -> dict[str, dict]:
        """Get vectors with open disputes. Returns {vector: {expected, reported}}."""
        try:
            cursor = self.conn.cursor()
            cursor.execute("""
                SELECT vector, expected_value, reported_value
                FROM calibration_disputes
                WHERE status = 'open'
                ORDER BY created_at DESC
            """)
            disputed = {}
            for row in cursor.fetchall():
                if row[0] not in disputed:
                    disputed[row[0]] = {
                        'expected': row[1],
                        'reported': row[2],
                    }
            return disputed
        except Exception:
            return {}

    def update_grounded_beliefs(
        self,
        session_id: str,
        assessment: GroundedAssessment,
        phase: str = "combined",
    ) -> dict[str, dict]:
        """
        Update grounded beliefs from a GroundedAssessment.

        For each grounded vector estimate, performs a Bayesian update using
        the objective evidence value as the observation.

        Disputed vectors get 4x observation variance (less trusted evidence)
        until the dispute is resolved.

        Returns dict of vector → update details.
        """
        cursor = self.conn.cursor()

        # Get AI ID
        cursor.execute(
            "SELECT ai_id FROM sessions WHERE session_id = ?",
            (session_id,),
        )
        row = cursor.fetchone()
        if not row:
            return {}
        ai_id = row[0]

        current_beliefs = self.get_grounded_beliefs(ai_id)
        disputed_vectors = self._get_disputed_vectors()
        updates = {}

        for vector_name, estimate in assessment.grounded.items():
            if vector_name in UNGROUNDABLE_VECTORS:
                continue

            belief = current_beliefs.get(vector_name)
            if belief:
                prior_mean = belief.mean
                prior_var = belief.variance
                evidence_count = belief.evidence_count
            else:
                prior_mean = self.DEFAULT_PRIOR_MEAN
                prior_var = self.DEFAULT_PRIOR_VARIANCE
                evidence_count = 0

            # Scale observation variance by evidence confidence
            # High-confidence evidence gets lower variance (more trusted)
            obs_var = self.OBSERVATION_VARIANCE / max(estimate.confidence, 0.1)

            # Disputed vectors: 4x observation variance (less weight on suspect evidence)
            is_disputed = vector_name in disputed_vectors
            if is_disputed:
                obs_var *= 4.0
                logger.debug(
                    f"Vector '{vector_name}' has open dispute — "
                    f"observation variance increased 4x to {obs_var:.4f}"
                )

            # Bayesian update
            posterior_mean = (
                (prior_var * estimate.estimated_value + obs_var * prior_mean)
                / (prior_var + obs_var)
            )
            posterior_var = 1.0 / (1.0 / prior_var + 1.0 / obs_var)
            new_evidence_count = evidence_count + estimate.evidence_count

            # Self-referential comparison
            self_val = assessment.self_assessed.get(vector_name)
            divergence = None
            if self_val is not None:
                divergence = round(self_val - posterior_mean, 4)

            # Store
            belief_id = str(uuid.uuid4())
            cursor.execute("""
                INSERT INTO grounded_beliefs (
                    belief_id, session_id, ai_id, vector_name,
                    mean, variance, evidence_count,
                    last_observation, last_observation_source,
                    self_referential_mean, divergence, last_updated,
                    phase
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                belief_id, session_id, ai_id, vector_name,
                posterior_mean, posterior_var, new_evidence_count,
                estimate.estimated_value, estimate.primary_source,
                self_val, divergence,
                datetime.now().timestamp(),
                phase,
            ))

            updates[vector_name] = {
                'prior_mean': prior_mean,
                'prior_variance': prior_var,
                'observation': estimate.estimated_value,
                'observation_source': estimate.primary_source,
                'posterior_mean': posterior_mean,
                'posterior_variance': posterior_var,
                'evidence_count': new_evidence_count,
                'self_assessed': self_val,
                'divergence': divergence,
            }

        self.conn.commit()
        return updates

    def store_evidence(
        self,
        bundle: EvidenceBundle,
    ) -> int:
        """Store raw evidence items for audit trail."""
        cursor = self.conn.cursor()
        stored = 0

        for item in bundle.items:
            evidence_id = str(uuid.uuid4())
            cursor.execute("""
                INSERT INTO verification_evidence (
                    evidence_id, session_id, source, metric_name,
                    raw_value, normalized_value, quality,
                    supports_vectors, collected_at, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                evidence_id,
                bundle.session_id,
                item.source,
                item.metric_name,
                json.dumps(item.raw_value),
                item.value,
                item.quality.value,
                json.dumps(item.supports_vectors),
                bundle.collection_timestamp,
                json.dumps(item.metadata) if item.metadata else None,
            ))
            stored += 1

        self.conn.commit()
        return stored

    def store_verification(
        self,
        session_id: str,
        assessment: GroundedAssessment,
        bundle: EvidenceBundle,
        domain: str | None = None,
        goal_id: str | None = None,
        phase: str = "combined",
    ) -> str:
        """Store a complete grounded verification record."""
        cursor = self.conn.cursor()

        # Get AI ID
        cursor.execute(
            "SELECT ai_id FROM sessions WHERE session_id = ?",
            (session_id,),
        )
        row = cursor.fetchone()
        ai_id = row[0] if row else "unknown"

        # Serialize grounded estimates
        grounded_data = {}
        for name, est in assessment.grounded.items():
            grounded_data[name] = {
                'value': est.estimated_value,
                'confidence': est.confidence,
                'evidence_count': est.evidence_count,
                'source': est.primary_source,
            }

        verification_id = str(uuid.uuid4())

        # B3: observed_vectors stores service-computed vectors (what we used
        # to call "grounded"). grounded_rationale stores AI's reasoning.
        # criticality + compliance_status from the assessment's A3 fields.
        observed_json = json.dumps(grounded_data)
        grounded_rationale = getattr(assessment, 'grounded_rationale', None)
        criticality = getattr(assessment, 'criticality', None)
        compliance_status = getattr(assessment, 'calibration_status', 'grounded')

        cursor.execute("""
            INSERT INTO grounded_verifications (
                verification_id, session_id, ai_id,
                self_assessed_vectors, grounded_vectors, calibration_gaps,
                grounded_coverage, overall_calibration_score,
                evidence_count, sources_available, sources_failed,
                domain, goal_id, phase,
                observed_vectors, grounded_rationale, criticality, compliance_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            verification_id,
            session_id,
            ai_id,
            json.dumps(assessment.self_assessed),
            json.dumps(grounded_data),  # legacy column — keeps working
            json.dumps(assessment.calibration_gaps),
            assessment.grounded_coverage,
            assessment.overall_calibration_score,
            len(bundle.items),
            json.dumps(bundle.sources_available),
            json.dumps(bundle.sources_failed),
            domain,
            goal_id,
            phase,
            observed_json,      # A3: new column with correct name
            grounded_rationale, # B3: AI's reasoning
            criticality,        # A3: domain criticality
            compliance_status,  # A3: compliance loop state
        ))

        self.conn.commit()
        return verification_id

    def get_calibration_divergence(self, ai_id: str) -> dict[str, dict]:
        """
        Compare self-referential and grounded calibration tracks.

        Returns per-vector comparison showing where the two tracks disagree.
        """
        from ..bayesian_beliefs import BayesianBeliefManager

        self_ref_manager = BayesianBeliefManager(self.db)
        self_ref_beliefs = self_ref_manager.get_beliefs(ai_id)
        grounded_beliefs = self.get_grounded_beliefs(ai_id)

        divergence = {}
        for vector in self.TRACKED_VECTORS:
            if vector in UNGROUNDABLE_VECTORS:
                continue

            self_ref = self_ref_beliefs.get(vector)
            grounded = grounded_beliefs.get(vector)

            if self_ref and grounded and grounded.evidence_count > 0:
                divergence[vector] = {
                    'self_referential_mean': self_ref.mean,
                    'grounded_mean': grounded.mean,
                    'gap': round(self_ref.mean - grounded.mean, 4),
                    'self_ref_evidence': self_ref.evidence_count,
                    'grounded_evidence': grounded.evidence_count,
                    'grounded_variance': grounded.variance,
                }

        return divergence

    def get_grounded_adjustments(self, ai_id: str) -> dict[str, float]:
        """
        Get calibration adjustments based on grounded evidence.

        Like BayesianBeliefManager.get_calibration_adjustments() but
        grounded in objective evidence.
        """
        beliefs = self.get_grounded_beliefs(ai_id)
        adjustments = {}

        from ..bayesian_beliefs import BayesianBeliefManager
        max_correction = BayesianBeliefManager.MAX_CORRECTION_MAGNITUDE

        for vector, belief in beliefs.items():
            if belief.evidence_count >= 3:
                adjustment = belief.mean - self.DEFAULT_PRIOR_MEAN
                evidence_weight = min(belief.evidence_count / 10.0, 1.0)
                raw = round(adjustment * evidence_weight, 4)
                # Cap correction magnitude (same limit as self-referential track)
                capped = max(-max_correction, min(max_correction, raw))
                adjustments[vector] = capped

        return adjustments

    def export_grounded_calibration(
        self,
        ai_id: str,
        git_root: str | None = None,
        phase_weights: dict | None = None,
        holistic_calibration_score: float | None = None,
        holistic_gaps: dict | None = None,
        insights: list | None = None,
    ) -> bool:
        """
        Export grounded calibration to .breadcrumbs.yaml as a new section.

        Does NOT replace the existing `calibration:` section — adds a
        parallel `grounded_calibration:` section for comparison.
        """
        import os
        import subprocess

        if not git_root:
            try:
                result = subprocess.run(
                    ['git', 'rev-parse', '--show-toplevel'],
                    capture_output=True, text=True, timeout=5,
                )
                if result.returncode == 0:
                    git_root = result.stdout.strip()
                else:
                    return False
            except Exception:
                return False

        breadcrumbs_path = os.path.join(git_root, '.breadcrumbs.yaml')

        beliefs = self.get_grounded_beliefs(ai_id)
        adjustments = self.get_grounded_adjustments(ai_id)
        divergence = self.get_calibration_divergence(ai_id)

        if not beliefs:
            return False

        total_evidence = sum(b.evidence_count for b in beliefs.values())
        if total_evidence == 0:
            return False

        # Compute grounded coverage (fraction of vectors with evidence)
        grounded_count = sum(
            1 for b in beliefs.values()
            if b.evidence_count > 0
        )
        coverage = grounded_count / len(
            [v for v in self.TRACKED_VECTORS if v not in UNGROUNDABLE_VECTORS]
        )

        # Build YAML
        timestamp = datetime.now().isoformat()
        lines = [
            "\n# Grounded calibration (auto-updated by Empirica post-test verification)\n",
            "# NOTE: These scores are drift indicators from deterministic proxies, not\n",
            "# ground truth. They detect systematic bias patterns over time but cannot\n",
            "# fully measure holistic epistemic state. Use alongside self-assessment,\n",
            "# not as a replacement. See dual-track calibration philosophy in docs.\n",
            "grounded_calibration:\n",
            f'  last_updated: "{timestamp}"\n',
            f"  ai_id: {ai_id}\n",
            f"  observations: {total_evidence}\n",
            f"  grounded_coverage: {coverage:.2f}\n",
        ]

        # Divergence section (grounded vs self-referential)
        if divergence:
            lines.append("  divergence:\n")
            sorted_div = sorted(
                divergence.items(),
                key=lambda x: abs(x[1]['gap']),
                reverse=True,
            )
            for vector, data in sorted_div:
                sign = '+' if data['gap'] >= 0 else ''
                lines.append(f"    {vector}: {sign}{data['gap']:.2f}\n")

        # Ungrounded vectors
        lines.append(
            f"  ungrounded: [{', '.join(sorted(UNGROUNDABLE_VECTORS))}]\n"
        )

        # Grounded bias corrections
        if adjustments:
            lines.append("  grounded_bias_corrections:\n")
            sorted_adj = sorted(
                adjustments.items(),
                key=lambda x: abs(x[1]),
                reverse=True,
            )
            for vector, adj in sorted_adj:
                sign = '+' if adj >= 0 else ''
                lines.append(f"    {vector}: {sign}{adj:.2f}\n")

        # Phase-weighted holistic calibration
        if phase_weights:
            lines.append("  phase_weights:\n")
            lines.append(f"    noetic: {phase_weights.get('noetic', 0.5)}\n")
            lines.append(f"    praxic: {phase_weights.get('praxic', 0.5)}\n")
            lines.append(f"    source: {phase_weights.get('source', 'unknown')}\n")
        if holistic_calibration_score is not None:
            lines.append(f"  holistic_calibration_score: {holistic_calibration_score:.4f}\n")
        if holistic_gaps:
            lines.append("  holistic_gaps:\n")
            sorted_hg = sorted(holistic_gaps.items(), key=lambda x: abs(x[1]), reverse=True)
            for vector, gap in sorted_hg:
                sign = '+' if gap >= 0 else ''
                lines.append(f"    {vector}: {sign}{gap:.4f}\n")

        # Calibration insights (feedback loop for method improvement)
        if insights:
            lines.append("  insights:\n")
            for insight in insights[:5]:  # Cap at 5 most relevant
                # Handle both CalibrationInsight objects and dicts
                _get = (lambda k: insight.get(k, '')) if isinstance(insight, dict) else (lambda k: getattr(insight, k, ''))
                lines.append(f"    - vector: {_get('vector')}\n")
                lines.append(f"      phase: {_get('phase')}\n")
                lines.append(f"      pattern: {_get('pattern')}\n")
                sev = _get('severity')
                lines.append(f"      severity: {sev:.2f}\n" if isinstance(sev, (int, float)) else f"      severity: {sev}\n")
                lines.append(f"      description: \"{_get('description')}\"\n")
                lines.append(f"      suggestion: \"{_get('suggestion')}\"\n")

        yaml_block = ''.join(lines)

        # Read existing file, find/replace grounded_calibration section
        try:
            existing_lines = []
            if os.path.exists(breadcrumbs_path):
                with open(breadcrumbs_path) as f:
                    existing_lines = f.readlines()

            section_start = -1
            section_end = -1
            in_section = False

            for i, line in enumerate(existing_lines):
                if '# Grounded calibration' in line and section_start == -1:
                    section_start = i
                elif line.strip().startswith('grounded_calibration:'):
                    if section_start == -1:
                        section_start = i
                    in_section = True
                elif in_section and line.strip() and not line.startswith(' ') and not line.startswith('\t'):
                    section_end = i
                    break

            if in_section and section_end == -1:
                section_end = len(existing_lines)

            if section_start >= 0:
                new_lines = (
                    existing_lines[:section_start]
                    + [yaml_block]
                    + existing_lines[section_end:]
                )
            elif existing_lines:
                new_lines = existing_lines + [yaml_block]
            else:
                new_lines = [yaml_block]

            with open(breadcrumbs_path, 'w') as f:
                f.writelines(new_lines)

            return True
        except Exception as e:
            logger.debug(f"Failed to export grounded calibration: {e}")
            return False


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
        return _build_insufficient_evidence_response(
            phase=phase,
            vectors=vectors,
            bundle=None,
            grounded_coverage=0.0,
            reason=(
                "work_type=remote-ops: local Sentinel has no signal "
                "for this work by declaration"
            ),
            status="ungrounded_remote_ops",
            note="Remote work by declaration. Self-assessment stands.",
        )

    # Release short-circuit: release transactions are mechanical pipelines
    # (merge, build, test, publish). No epistemic work to calibrate.
    if work_type == "release":
        return _build_insufficient_evidence_response(
            phase=phase,
            vectors=vectors,
            bundle=None,
            grounded_coverage=0.0,
            reason="work_type=release: mechanical pipeline, no epistemic work to calibrate",
            status="ungrounded_release",
            note="Release pipeline by declaration. Self-assessment stands.",
        )

    collector = PostTestCollector(
        session_id=session_id,
        project_id=project_id,
        db=db,
        phase=phase,
        check_timestamp=check_timestamp,
        evidence_profile=evidence_profile,
        work_context=work_context,
        preflight_timestamp=preflight_timestamp,
        transaction_id=transaction_id,
    )
    bundle = collector.collect_all()

    # Empty bundle is no longer a silent None return — surface it as
    # insufficient_evidence so the AI knows what happened. Reaches the same
    # response builder as the threshold gate below.
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

    # Effective coverage: weight-aware coverage computation.
    # Raw coverage = grounded_vectors / 13 (all vectors equally).
    # Effective coverage = grounded_vectors weighted by category importance.
    # A noetic phase with know+context+signal grounded and research work_type
    # has high effective coverage (those ARE the important vectors for research)
    # even though raw coverage is only 3/13 = 0.23.
    effective_coverage = assessment.grounded_coverage  # default: use raw
    try:
        from empirica.core.post_test.mapper import _load_domain_weights
        config = _load_domain_weights(domain or "default", work_type=work_type)
        cat_weights = config["category_weights"]
        vector_map = config["vector_category_map"]

        # Which vectors have grounded estimates?
        grounded_vectors = set(assessment.grounded.keys())

        # Sum category weights for categories that have ANY grounded vector
        covered_weight = 0.0
        total_weight = 0.0
        for cat in ("foundation", "comprehension", "execution", "meta"):
            cat_w = cat_weights.get(cat, 0.25)
            total_weight += cat_w
            # Does any vector in this category have grounded evidence?
            cat_vectors = [v for v, c in vector_map.items() if c == cat]
            if any(v in grounded_vectors for v in cat_vectors):
                covered_weight += cat_w

        if total_weight > 0:
            # Category breadth: how many of the 4 categories have evidence?
            # Single-category coverage is risky even if high-weight — apply
            # breadth penalty to require evidence across multiple categories.
            categories_covered = sum(
                1 for cat in ("foundation", "comprehension", "execution", "meta")
                if any(v in grounded_vectors for v in
                       [v2 for v2, c in vector_map.items() if c == cat])
            )
            breadth_factor = categories_covered / 4.0  # 1 cat = 0.25, 2 = 0.50, etc.
            effective_coverage = (covered_weight / total_weight) * breadth_factor
    except Exception:
        pass  # Fall back to raw coverage

    # Coverage threshold gate. If grounded_coverage is below the threshold,
    # the bundle had items but they didn't ground enough vectors to produce
    # statistically meaningful calibration. Halt and surface as insufficient.
    # Storage operations below are skipped — the trajectory and verifications
    # tables only contain grounded data.
    if effective_coverage < INSUFFICIENT_EVIDENCE_THRESHOLD:
        return _build_insufficient_evidence_response(
            phase=phase,
            vectors=vectors,
            bundle=bundle,
            grounded_coverage=effective_coverage,
            reason=(
                f"grounded_coverage {assessment.grounded_coverage:.2f} < "
                f"threshold {INSUFFICIENT_EVIDENCE_THRESHOLD}"
            ),
        )

    manager = GroundedCalibrationManager(db)
    updates = manager.update_grounded_beliefs(session_id, assessment, phase=phase)

    manager.store_evidence(bundle)
    verification_id = manager.store_verification(
        session_id, assessment, bundle,
        domain=domain, goal_id=goal_id, phase=phase,
    )

    from .trajectory_tracker import TrajectoryTracker
    tracker = TrajectoryTracker(db)
    tracker.record_trajectory_point(
        session_id, assessment,
        domain=domain, goal_id=goal_id, phase=phase,
    )

    return {
        'verification_id': verification_id,
        'phase': phase,
        'evidence_count': len(bundle.items),
        'sources': bundle.sources_available,
        'sources_failed': bundle.sources_failed,
        'sources_empty': getattr(bundle, 'sources_empty', []),
        'source_errors': getattr(bundle, 'source_errors', {}),
        'grounded_coverage': round(assessment.grounded_coverage, 2),
        'calibration_score': assessment.overall_calibration_score,
        'calibration_status': 'grounded',
        'gaps': assessment.calibration_gaps,
        # Vectors the instrument couldn't sample for this work_type.
        # Their self-assessment stands; no fabricated grounded value, no
        # false drift in calibration_trajectory. Surfaced here so the AI
        # can see WHICH vectors were skipped and why — supporting honest
        # collaboration between the AI and the measurement layer.
        'insufficient_evidence_vectors': getattr(
            assessment, 'insufficient_evidence_vectors', []
        ) or [],
        'updates': {
            v: {
                'observation': u['observation'],
                'self_assessed': u['self_assessed'],
                'divergence': u['divergence'],
            }
            for v, u in updates.items()
        },
    }


def _compute_phase_weights(
    phase_tool_counts: dict[str, int] | None,
    phase_boundary: dict | None,
    results: dict,
) -> dict:
    """Compute noetic/praxic weights from tool classification counts.

    Returns {'noetic': float, 'praxic': float, 'source': str}.
    Weights sum to 1.0. Floor of 0.1 for any phase with evidence.
    """
    if not phase_tool_counts or not results:
        return {'noetic': 0.5, 'praxic': 0.5, 'source': 'default'}

    noetic_calls = phase_tool_counts.get('noetic_tool_calls', 0)
    praxic_calls = phase_tool_counts.get('praxic_tool_calls', 0)
    total = noetic_calls + praxic_calls

    if total == 0:
        return {'noetic': 0.5, 'praxic': 0.5, 'source': 'no_tool_data'}

    if phase_boundary and phase_boundary.get('noetic_only'):
        return {'noetic': 1.0, 'praxic': 0.0, 'source': 'noetic_only'}

    noetic_w = noetic_calls / total
    praxic_w = praxic_calls / total

    # Floor: minimum 0.1 weight for any phase that has evidence
    has_noetic_evidence = 'noetic' in results
    has_praxic_evidence = 'praxic' in results

    if has_noetic_evidence and noetic_w < 0.1:
        noetic_w = 0.1
        praxic_w = 0.9
    if has_praxic_evidence and praxic_w < 0.1:
        praxic_w = 0.1
        noetic_w = 0.9

    return {'noetic': round(noetic_w, 4), 'praxic': round(praxic_w, 4), 'source': 'tool_classification'}


def run_grounded_verification(
    session_id: str,
    postflight_vectors: dict[str, float],
    db,
    project_id: str | None = None,
    domain: str | None = None,
    goal_id: str | None = None,
    phase_boundary: dict | None = None,
    evidence_profile: str | None = None,
    phase_tool_counts: dict[str, int] | None = None,
    work_context: str | None = None,
    work_type: str | None = None,
    per_vector_weights: dict[str, dict[str, float]] | None = None,
    transaction_id: str | None = None,
) -> dict | None:
    """
    Full grounded verification pipeline.

    Called after POSTFLIGHT: collect → map → update → store → trajectory → export.

    Phase-aware when phase_boundary is provided (from detect_phase_boundary()):
    - Splits into noetic (PREFLIGHT→CHECK) and praxic (CHECK→POSTFLIGHT) passes
    - Each phase gets independent evidence collection and calibration
    - Falls back to combined when no CHECK boundary exists

    phase_tool_counts: {'noetic_tool_calls': int, 'praxic_tool_calls': int}
    from Sentinel's phase-split counting. Used to weight the holistic score.

    Returns verification summary dict, or None on failure.
    """
    try:
        results = {}

        if phase_boundary and phase_boundary.get("has_check"):
            check_ts = phase_boundary.get("proceed_check_timestamp")
            preflight_ts = phase_boundary.get("preflight_timestamp")
            noetic_only = phase_boundary.get("noetic_only", False)

            # Noetic vectors: delta from PREFLIGHT to CHECK
            preflight_vectors = phase_boundary.get("preflight_vectors") or {}
            check_vectors = phase_boundary.get("proceed_check_vectors") or {}

            # Noetic self-assessment = CHECK vectors (what AI claimed at CHECK)
            noetic_self = {}
            for k, v in check_vectors.items():
                if v is not None:
                    noetic_self[k] = v

            # Extract phase-specific Tier 2 weights
            noetic_weights = (per_vector_weights or {}).get('noetic')
            praxic_weights = (per_vector_weights or {}).get('praxic')

            if noetic_self:
                noetic_result = _run_single_phase_verification(
                    session_id, noetic_self, db,
                    phase="noetic",
                    project_id=project_id,
                    domain=domain, goal_id=goal_id,
                    check_timestamp=check_ts,
                    evidence_profile=evidence_profile,
                    work_context=work_context,
                    work_type=work_type,
                    preflight_timestamp=preflight_ts,
                    per_vector_weights=noetic_weights,
                    transaction_id=transaction_id,
                )
                if noetic_result:
                    results["noetic"] = noetic_result

            # Praxic: only if not noetic-only (had a proceed CHECK)
            if not noetic_only:
                praxic_result = _run_single_phase_verification(
                    session_id, postflight_vectors, db,
                    phase="praxic",
                    project_id=project_id,
                    domain=domain, goal_id=goal_id,
                    check_timestamp=check_ts,
                    evidence_profile=evidence_profile,
                    work_context=work_context,
                    work_type=work_type,
                    preflight_timestamp=preflight_ts,
                    per_vector_weights=praxic_weights,
                    transaction_id=transaction_id,
                )
                if praxic_result:
                    results["praxic"] = praxic_result
        else:
            # No phase boundary — combined mode (backward-compatible)
            # Use praxic weights as best default for combined mode
            combined_weights = (per_vector_weights or {}).get('praxic')
            combined_result = _run_single_phase_verification(
                session_id, postflight_vectors, db,
                phase="combined",
                project_id=project_id,
                domain=domain, goal_id=goal_id,
                evidence_profile=evidence_profile,
                work_context=work_context,
                work_type=work_type,
                per_vector_weights=combined_weights,
                transaction_id=transaction_id,
            )
            if combined_result:
                results["combined"] = combined_result

        if not results:
            return None

        # Build unified summary
        all_gaps = {}
        all_updates = {}
        total_evidence = 0
        all_sources = []
        all_failed = []
        verification_ids = []

        for phase_name, phase_result in results.items():
            total_evidence += phase_result['evidence_count']
            all_sources.extend(phase_result['sources'])
            all_failed.extend(phase_result['sources_failed'])
            verification_ids.append(phase_result['verification_id'])
            for v, gap in phase_result['gaps'].items():
                all_gaps[f"{phase_name}:{v}"] = gap
            for v, u in phase_result['updates'].items():
                all_updates[f"{phase_name}:{v}"] = u

        # Phase-weighted holistic calibration
        phase_weights = _compute_phase_weights(phase_tool_counts, phase_boundary, results)
        holistic_calibration_score = None
        holistic_gaps = {}

        # Filter to only grounded phases before computing holistic — non-grounded
        # phases (insufficient_evidence, ungrounded_remote_ops, ungrounded_release)
        # have calibration_score=None and gaps={} by design.
        grounded_results = {
            phase: r for phase, r in results.items()
            if r.get('calibration_status', 'grounded') == 'grounded'
        }

        if len(grounded_results) >= 2 and 'noetic' in grounded_results and 'praxic' in grounded_results:
            nw = phase_weights['noetic']
            pw = phase_weights['praxic']
            n_score = grounded_results['noetic'].get('calibration_score') or 0
            p_score = grounded_results['praxic'].get('calibration_score') or 0
            holistic_calibration_score = round(nw * n_score + pw * p_score, 4)

            # Weighted gaps per vector (strip phase prefix for holistic view)
            noetic_gaps = grounded_results['noetic'].get('gaps', {}) or {}
            praxic_gaps = grounded_results['praxic'].get('gaps', {}) or {}
            all_vectors = set(noetic_gaps.keys()) | set(praxic_gaps.keys())
            for v in all_vectors:
                n_gap = noetic_gaps.get(v, 0)
                p_gap = praxic_gaps.get(v, 0)
                holistic_gaps[v] = round(nw * n_gap + pw * p_gap, 4)
        elif len(grounded_results) == 1:
            # Single grounded phase (the other may be non-grounded or missing)
            only_result = next(iter(grounded_results.values()))
            holistic_calibration_score = only_result.get('calibration_score') or 0
            holistic_gaps = only_result.get('gaps', {}) or {}
        # else: zero grounded phases → holistic_calibration_score stays None
        # and holistic_gaps stays empty. The AI's self-assessment stands.

        # Calibration insights: analyze recent verifications for systemic patterns
        calibration_insights = []
        try:
            from empirica.core.post_test.calibration_insights import CalibrationInsightsAnalyzer
            analyzer = CalibrationInsightsAnalyzer(db, session_id, lookback=10)
            calibration_insights = analyzer.analyze()
            if calibration_insights:
                analyzer.store_insights(calibration_insights)
                logger.debug(
                    f"Calibration insights: {len(calibration_insights)} patterns detected"
                )
        except Exception as e:
            logger.debug(f"Calibration insights analysis failed (non-fatal): {e}")

        # Export to .breadcrumbs.yaml (after holistic computation so weights are included)
        manager = GroundedCalibrationManager(db)
        cursor = db.conn.cursor()
        cursor.execute(
            "SELECT ai_id FROM sessions WHERE session_id = ?",
            (session_id,),
        )
        row = cursor.fetchone()
        if row:
            manager.export_grounded_calibration(
                row[0],
                phase_weights=phase_weights,
                holistic_calibration_score=holistic_calibration_score,
                holistic_gaps=holistic_gaps,
                insights=calibration_insights,
            )

        return {
            'verification_ids': verification_ids,
            'phase_aware': phase_boundary is not None and phase_boundary.get("has_check", False),
            'phase_weights': phase_weights,
            'holistic_calibration_score': holistic_calibration_score,
            'holistic_gaps': holistic_gaps,
            'phases': results,
            'evidence_count': total_evidence,
            'sources': list(set(all_sources)),
            'sources_failed': list(set(all_failed)),
            'gaps': all_gaps,
            'updates': all_updates,
            'insights': [
                {
                    'vector': i.vector,
                    'phase': i.phase,
                    'pattern': i.pattern,
                    'severity': i.severity,
                    'description': i.description,
                    'suggestion': i.suggestion,
                }
                for i in calibration_insights
            ],
        }

    except Exception as e:
        logger.warning(f"Grounded verification failed (non-fatal): {e}")
        return None
