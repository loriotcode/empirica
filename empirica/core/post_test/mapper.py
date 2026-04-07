"""
Evidence-to-Vector Mapper

Maps objective evidence items to estimated vector values.
Only produces estimates for vectors with sufficient evidence.
Uses weighted aggregation when multiple evidence items support the same vector.

Calibration scoring uses domain-aware category weights (Tier 1) from
confidence_weights.yaml. Per-vector weights (Tier 2) can be passed in
from project.yaml for per-project, per-phase dynamic weighting.

Ungroundable vectors (no objective signal): engagement.
Coherence and density are now grounded via code quality metrics (ruff, radon).
These keep self-referential calibration.

NOTE on proxy limitations: All evidence sources here are deterministic proxies.
Test pass rates approximate 'know' but miss conceptual understanding depth.
Git metrics approximate 'do' but miss quality of thought behind changes.
Artifact counts approximate 'signal' but can't judge relevance of what was logged.
The calibration gaps computed here are drift indicators — useful for detecting
systematic bias patterns over time, but not ground truth for any single transaction.
"""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import yaml

from .collector import EvidenceBundle, EvidenceItem, EvidenceQuality

logger = logging.getLogger(__name__)


QUALITY_WEIGHTS = {
    EvidenceQuality.OBJECTIVE: 1.0,
    EvidenceQuality.SEMI_OBJECTIVE: 0.7,
    EvidenceQuality.INFERRED: 0.4,
}

# Vectors with no objective signal. Code quality metrics (ruff, radon) now
# provide grounding for coherence and density, leaving only engagement.
UNGROUNDABLE_VECTORS = {"engagement"}

# Evidence source relevance by work_type.
#
# Keys are the actual source IDs emitted by PostTestCollector EvidenceItems:
#   noetic, goals, artifacts, issues, triage, codebase_model, non_git_files,
#   sentinel, pytest, git, code_quality
#
# Semantics:
#   missing source key   → defaults to 1.0 (full weight, neutral)
#   value == 0.0         → EXCLUDED. The source is instrument-blind for this
#                          work_type and is filtered out of the evidence pool
#                          entirely. Vectors that ONLY had evidence from
#                          excluded sources will be marked insufficient_evidence
#                          and will fall back to the AI's self-assessment
#                          (no fabricated grounded value, no false drift).
#   0.0 < value < 1.0    → down-weight (still contributes but reduced)
#   value > 1.0          → up-weight (primary evidence for this work_type)
#
# Design principle: when the instrument can't sample the work, report honest
# absence (None) rather than computing a misleading score from absent signal.
# See: docs/architecture/PHASE_AWARE_CALIBRATION.md and the metric-sycophancy
# discussion (2026-04-07).
#
# History: This dict previously used keys like "git_metrics", "test_results",
# "goal_completion", "artifact_counts" which DID NOT MATCH the source IDs
# emitted by collector.py — making the entire work_type scaling a silent no-op.
# Fixed 2026-04-07 along with adding the 0.0 = excluded semantic.
WORK_TYPE_RELEVANCE: dict[str, dict[str, float]] = {
    # code: baseline — all sources at default 1.0 weight
    "code": {},

    # infra: infrastructure changes (yaml, dockerfile, k8s manifests)
    # Code quality and pytest aren't great signals; goal completion is.
    "infra": {
        "code_quality": 0.3, "pytest": 0.3, "codebase_model": 0.3,
        "goals": 1.3, "git": 1.0, "artifacts": 1.1,
    },

    # research: experimental investigation, exploring possibilities
    # The deliverable is artifacts (findings, unknowns), not code.
    "research": {
        "git": 0.0, "code_quality": 0.0, "codebase_model": 0.0,
        "pytest": 0.0, "non_git_files": 0.0,
        "artifacts": 1.5, "goals": 1.0, "noetic": 1.5,
    },

    # release: publish pipeline (twine, docker, gh release, homebrew tap)
    # Git only sees mechanical version sweep commit. Code unchanged. Codebase
    # unchanged. The actual work is invisible to in-repo sensors.
    "release": {
        "git": 0.0, "code_quality": 0.0, "codebase_model": 0.0,
        "non_git_files": 0.0,
        "goals": 1.5, "triage": 1.3, "pytest": 1.2, "artifacts": 1.0,
    },

    # debug: finding + fixing bugs
    # Tests are the strongest signal (regression caught/passed).
    "debug": {
        "pytest": 1.4, "triage": 1.3, "artifacts": 1.3,
        "code_quality": 0.5, "git": 1.0,
    },

    # config: configuration changes (settings.json, .env, yaml)
    # Code quality + pytest are usually irrelevant; goal completion matters.
    "config": {
        "code_quality": 0.0, "pytest": 0.3, "codebase_model": 0.0,
        "goals": 1.2, "git": 1.0, "artifacts": 1.1,
    },

    # docs: documentation work (markdown, doc-only changes)
    # Markdown isn't ruff-checked; pytest doesn't apply; codebase model unchanged.
    "docs": {
        "code_quality": 0.0, "pytest": 0.0, "codebase_model": 0.0,
        "git": 1.0, "non_git_files": 1.2, "goals": 1.2, "artifacts": 1.0,
    },

    # data: data work (CSVs, datasets, schemas)
    "data": {
        "code_quality": 0.0, "pytest": 0.5, "codebase_model": 0.0,
        "non_git_files": 1.3, "goals": 1.2, "artifacts": 1.0,
    },

    # comms: writing messages, sending communications, outreach
    # No code touched, no tests, no codebase change.
    "comms": {
        "git": 0.0, "code_quality": 0.0, "pytest": 0.0,
        "codebase_model": 0.0, "non_git_files": 0.0,
        "goals": 1.5, "artifacts": 1.0,
    },

    # design: design docs, architecture proposals, mockups
    # Mostly markdown + diagrams, not code.
    "design": {
        "code_quality": 0.0, "pytest": 0.0, "codebase_model": 0.0,
        "git": 1.0, "non_git_files": 1.2, "artifacts": 1.4, "goals": 1.2,
    },

    # audit: read-only investigation (code review, security audit, doc audit)
    # Audits don't write code, don't change quality metrics, don't restructure.
    # The deliverable is artifacts (findings, decisions, recommendations).
    "audit": {
        "git": 0.0, "code_quality": 0.0, "codebase_model": 0.0,
        "non_git_files": 0.0,
        "artifacts": 1.5, "noetic": 1.3, "goals": 1.2, "triage": 1.0,
    },
}


@dataclass
class GroundedVectorEstimate:
    """An objectively grounded estimate for a single vector."""
    vector_name: str
    estimated_value: float
    confidence: float
    evidence_count: int
    primary_source: str
    is_grounded: bool = True


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
    """
    session_id: str
    self_assessed: dict[str, float]
    grounded: dict[str, GroundedVectorEstimate]
    calibration_gaps: dict[str, float]
    grounded_coverage: float
    overall_calibration_score: float
    phase: str = "combined"  # "noetic", "praxic", or "combined"
    insufficient_evidence_vectors: list[str] = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.insufficient_evidence_vectors is None:
            self.insufficient_evidence_vectors = []


def _load_domain_weights(domain: str = "default") -> dict[str, Any]:
    """Load domain category weights and vector-category map from confidence_weights.yaml.

    Args:
        domain: Domain name (software, consulting, research, operations, default)

    Returns:
        Dict with 'category_weights' and 'vector_category_map'
    """
    config_path = Path(__file__).parent.parent.parent / "config" / "mco" / "confidence_weights.yaml"
    defaults = {
        "category_weights": {"foundation": 0.35, "comprehension": 0.25, "execution": 0.25, "engagement": 0.15},
        "vector_category_map": {
            "know": "foundation", "do": "foundation", "context": "foundation",
            "clarity": "comprehension", "coherence": "comprehension",
            "signal": "comprehension", "density": "comprehension",
            "state": "execution", "change": "execution",
            "completion": "execution", "impact": "execution",
            "engagement": "engagement",
            "uncertainty": "engagement",
        },
    }
    if not config_path.exists():
        return defaults

    try:
        with open(config_path) as f:
            config = yaml.safe_load(f) or {}

        domain_weights = config.get("domain_category_weights", {})
        category_weights = domain_weights.get(domain, domain_weights.get("default", defaults["category_weights"]))
        vector_map = config.get("vector_category_map", defaults["vector_category_map"])
        return {"category_weights": category_weights, "vector_category_map": vector_map}
    except Exception as e:
        logger.warning(f"Failed to load domain weights: {e}")
        return defaults


def _compute_weighted_calibration(
    calibration_gaps: dict[str, float],
    domain: str = "default",
    per_vector_weights: dict[str, float] | None = None,
) -> float:
    """Compute category-weighted calibration score.

    Tier 1: Domain category weights determine how much each category contributes.
    Tier 2: Per-vector weights (optional) scale individual vector gaps within categories.

    Args:
        calibration_gaps: Dict of vector_name → gap (self - grounded)
        domain: Domain for Tier 1 category weights
        per_vector_weights: Optional Tier 2 per-vector weights (from project.yaml)

    Returns:
        Weighted calibration score (lower = better calibrated)
    """
    if not calibration_gaps:
        return 0.0

    config = _load_domain_weights(domain)
    category_weights = config["category_weights"]
    vector_map = config["vector_category_map"]

    # Group gaps by category
    category_gaps: dict[str, list[tuple[str, float]]] = {}
    for vector_name, gap in calibration_gaps.items():
        category = vector_map.get(vector_name, "foundation")
        if category not in category_gaps:
            category_gaps[category] = []

        # Apply per-vector weight (Tier 2) if provided
        vector_weight = 1.0
        if per_vector_weights:
            vector_weight = per_vector_weights.get(vector_name, 1.0)

        category_gaps[category].append((vector_name, abs(gap) * vector_weight))

    # Weighted mean: category weight * mean abs gap within category
    total_score = 0.0
    total_weight = 0.0
    for category, gaps in category_gaps.items():
        cat_weight = category_weights.get(category, 0.25)
        cat_mean = sum(g for _, g in gaps) / len(gaps)
        total_score += cat_weight * cat_mean
        total_weight += cat_weight

    # Normalize by actual weight used (not all categories may have grounded vectors)
    if total_weight > 0:
        return total_score / total_weight
    return 0.0


class EvidenceMapper:
    """Maps evidence bundles to grounded vector estimates."""

    def map_evidence(
        self,
        bundle: EvidenceBundle,
        self_assessed_vectors: dict[str, float],
        phase: str = "combined",
        domain: str = "default",
        per_vector_weights: dict[str, float] | None = None,
        work_type: str | None = None,
    ) -> GroundedAssessment:
        """Map evidence to grounded vector estimates and compare to self-assessment.

        Work-type-aware:
          - Sources with relevance == 0.0 are EXCLUDED for this work_type
            (instrument-blind: cannot sample the kind of work being done).
          - Sources with relevance > 0.0 contribute with that multiplier.
          - Vectors that ONLY had evidence from excluded sources are marked
            insufficient_evidence — no grounded value is computed, no gap
            is recorded, no false drift is written to the trajectory.
            The AI's self-assessment stands as the best available estimate.
        """
        # Work-type relevance profile (scales evidence weights by source relevance)
        relevance = WORK_TYPE_RELEVANCE.get(work_type, {}) if work_type else {}

        # Track which vectors have excluded-only evidence (instrument-blind for this work_type).
        # Vectors here saw evidence from at least one source, but every source was excluded.
        vectors_seen_in_excluded: set[str] = set()
        # Vectors here saw evidence from at least one INCLUDED source.
        vectors_with_included: set[str] = set()

        # Group evidence by supported vector, applying exclusion semantic
        vector_evidence: dict[str, list[tuple[EvidenceItem, float]]] = {}
        for item in bundle.items:
            quality_weight = QUALITY_WEIGHTS.get(item.quality, 0.5)
            # Apply work-type relevance scaling to evidence source.
            # 0.0 = explicitly excluded (instrument-blind for this work_type).
            # Missing source defaults to 1.0 (neutral).
            source_relevance = relevance.get(item.source, 1.0)
            if source_relevance <= 0.0:
                # Source is instrument-blind for this work_type. Skip it
                # entirely so it doesn't pull grounded estimates toward
                # absent-signal values. Record which vectors it would have
                # contributed to so we can detect "all-excluded" vectors.
                for vector in item.supports_vectors:
                    if vector not in UNGROUNDABLE_VECTORS:
                        vectors_seen_in_excluded.add(vector)
                continue

            weight = quality_weight * source_relevance
            for vector in item.supports_vectors:
                if vector not in vector_evidence:
                    vector_evidence[vector] = []
                vector_evidence[vector].append((item, weight))
                vectors_with_included.add(vector)

        # Compute grounded estimates via weighted average
        grounded = {}
        for vector_name, evidence_list in vector_evidence.items():
            if vector_name in UNGROUNDABLE_VECTORS:
                continue

            total_weight = sum(w for _, w in evidence_list)
            if total_weight == 0:
                continue

            weighted_value = sum(
                item.value * w for item, w in evidence_list
            ) / total_weight
            primary_source = max(evidence_list, key=lambda x: x[1])[0].source

            grounded[vector_name] = GroundedVectorEstimate(
                vector_name=vector_name,
                estimated_value=max(0.0, min(1.0, weighted_value)),
                confidence=min(1.0, total_weight / len(evidence_list)),
                evidence_count=len(evidence_list),
                primary_source=primary_source,
            )

        # Compute insufficient_evidence_vectors: vectors that had evidence
        # ONLY from excluded sources (no included evidence remained after filter).
        insufficient = sorted(vectors_seen_in_excluded - vectors_with_included)

        # Compute calibration gaps (self - grounded)
        # Positive = AI overestimates, Negative = AI underestimates.
        # Insufficient-evidence vectors are NOT included — no fabricated drift.
        calibration_gaps = {}
        for vector_name, estimate in grounded.items():
            self_val = self_assessed_vectors.get(vector_name, 0.5)
            calibration_gaps[vector_name] = round(
                self_val - estimate.estimated_value, 4
            )

        # Overall calibration score — domain-weighted (Tier 1 + optional Tier 2)
        overall_score = _compute_weighted_calibration(
            calibration_gaps, domain=domain, per_vector_weights=per_vector_weights,
        )

        return GroundedAssessment(
            session_id=bundle.session_id,
            self_assessed=self_assessed_vectors,
            grounded=grounded,
            calibration_gaps=calibration_gaps,
            grounded_coverage=bundle.coverage,
            overall_calibration_score=round(overall_score, 4),
            phase=phase,
            insufficient_evidence_vectors=insufficient,
        )
