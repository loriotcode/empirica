"""
Dynamic Thresholds — Brier Score Based Calibration Gating

Computes phase-aware CHECK gate thresholds using Brier score (a strictly proper
scoring rule) instead of MAE (improper — incentivizes extreme predictions).

Threshold model:
- Good calibration (low Brier reliability) → threshold stays at domain baseline
- Bad calibration (high Brier reliability) → threshold RAISED to compensate for bias
- Thresholds never go BELOW domain baselines — good calibration is not rewarded
  with a lower bar, it's rewarded with the system trusting the numbers as-is

Brier decomposition (Murphy 1973):
  BS = Reliability - Resolution + Uncertainty
  - Reliability: calibration error (lower = better calibrated, 0 = perfect)
  - Resolution: discrimination power (higher = better at distinguishing easy/hard)
  - Uncertainty: inherent domain difficulty (not controllable)

Only Reliability drives threshold inflation. Resolution and Uncertainty are diagnostic.

Self-correcting properties:
- Overconfidence → high reliability component → tighter gates → forced investigation
- Good calibration → reliability near 0 → gates stay at baseline → numbers trusted
- Phase-specific → noetic and praxic competence are independent axes

References:
- Brier (1950): "Verification of forecasts expressed in terms of probability"
- Murphy (1973): Brier score decomposition into reliability + resolution - uncertainty
- Sahoo et al. (NeurIPS 2021): "Reliable Decisions with Threshold Calibration"
- Gneiting & Raftery (2007): "Strictly Proper Scoring Rules, Prediction, and Estimation"
"""
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Domain baselines — the bar when calibration is good (trusted)
# These are the MINIMUM thresholds. Miscalibration only raises them.
DOMAIN_BASELINES = {
    "ready_know_threshold": 0.70,
    "ready_uncertainty_threshold": 0.35,
}

# Safety ceilings — worst miscalibration can't make gates impossible
SAFETY_CEILINGS = {
    "ready_know_threshold": 0.90,       # Never require > 90% know
    "ready_uncertainty_threshold": 0.15,  # Never require < 15% uncertainty
}

# Maximum inflation from miscalibration (prevents runaway tightening)
MAX_INFLATION = 0.20  # At worst, gates tighten by 20% beyond baseline


@dataclass
class BrierDecomposition:
    """Murphy (1973) decomposition of Brier score."""
    brier_score: float       # Overall: 0 = perfect, 1 = worst
    reliability: float       # Calibration error: 0 = perfectly calibrated
    resolution: float        # Discrimination: higher = better at distinguishing
    uncertainty: float       # Domain difficulty: not controllable
    n_predictions: int       # Sample size
    n_bins: int              # Bins used for decomposition


def compute_brier_score(predictions: List[Tuple[float, float]]) -> float:
    """Compute raw Brier score from (predicted, observed) pairs.

    Args:
        predictions: List of (predicted_probability, observed_outcome) tuples.
                    predicted is 0.0-1.0, observed is the grounded value 0.0-1.0.

    Returns:
        Brier score (0.0 = perfect, 1.0 = worst).
    """
    if not predictions:
        return 1.0  # Worst possible — no data
    return sum((p - o) ** 2 for p, o in predictions) / len(predictions)


def compute_brier_decomposition(
    predictions: List[Tuple[float, float]],
    n_bins: int = 10,
) -> BrierDecomposition:
    """Compute Murphy (1973) Brier score decomposition.

    Decomposes into: BS = Reliability - Resolution + Uncertainty

    Args:
        predictions: List of (predicted_probability, observed_outcome) tuples.
        n_bins: Number of bins for decomposition (default 10).

    Returns:
        BrierDecomposition with reliability, resolution, uncertainty components.
    """
    if not predictions:
        return BrierDecomposition(
            brier_score=1.0, reliability=1.0, resolution=0.0,
            uncertainty=0.25, n_predictions=0, n_bins=0,
        )

    n = len(predictions)

    # Overall observed mean (base rate)
    o_bar = sum(o for _, o in predictions) / n

    # Uncertainty: variance of outcomes (inherent difficulty)
    # For continuous outcomes, use sample variance
    uncertainty = sum((o - o_bar) ** 2 for _, o in predictions) / n

    # Bin predictions by predicted probability
    bins: Dict[int, List[Tuple[float, float]]] = {}
    for pred, obs in predictions:
        bin_idx = min(int(pred * n_bins), n_bins - 1)
        bins.setdefault(bin_idx, []).append((pred, obs))

    # Reliability: weighted average of (forecast_mean - observed_mean)^2 per bin
    reliability = 0.0
    resolution = 0.0
    for bin_idx, bin_items in bins.items():
        n_k = len(bin_items)
        f_k = sum(p for p, _ in bin_items) / n_k   # Mean forecast in bin
        o_k = sum(o for _, o in bin_items) / n_k    # Mean observed in bin

        reliability += (n_k / n) * (f_k - o_k) ** 2
        resolution += (n_k / n) * (o_k - o_bar) ** 2

    brier_score = reliability - resolution + uncertainty

    return BrierDecomposition(
        brier_score=round(max(0.0, brier_score), 6),
        reliability=round(reliability, 6),
        resolution=round(resolution, 6),
        uncertainty=round(uncertainty, 6),
        n_predictions=n,
        n_bins=len(bins),
    )


def compute_dynamic_thresholds(
    ai_id: str,
    db,
    base_thresholds: Optional[Dict] = None,
    min_transactions: int = 5,
    lookback: int = 20,
) -> Dict:
    """Compute phase-aware dynamic thresholds using Brier score reliability.

    Threshold model:
    - reliability near 0 → well calibrated → thresholds stay at domain baseline
    - reliability > 0 → miscalibrated → thresholds inflated proportionally
    - inflation = min(reliability * scale_factor, MAX_INFLATION)

    Args:
        ai_id: AI identifier (e.g., "claude-code")
        db: Database connection
        base_thresholds: Override domain baselines (default: 0.70 know, 0.35 uncertainty)
        min_transactions: Minimum trajectory points before enabling dynamic thresholds
        lookback: Number of recent trajectory points to analyze

    Returns:
        {
            "noetic": {
                "ready_know_threshold": float,
                "ready_uncertainty_threshold": float,
                "brier_score": float,
                "brier_reliability": float,   # Drives threshold inflation
                "brier_resolution": float,    # Diagnostic only
                "brier_uncertainty": float,   # Diagnostic only
                "threshold_inflation": float, # How much thresholds were raised
                "transactions_analyzed": int,
            },
            "praxic": { ... same ... },
            "source": "dynamic" | "static",
            "reason": str,
        }
    """
    base = base_thresholds or DOMAIN_BASELINES.copy()
    know_base = base.get("ready_know_threshold", 0.70)
    unc_base = base.get("ready_uncertainty_threshold", 0.35)

    static_phase = {
        "ready_know_threshold": know_base,
        "ready_uncertainty_threshold": unc_base,
        "brier_score": None,
        "brier_reliability": None,
        "brier_resolution": None,
        "brier_uncertainty": None,
        "threshold_inflation": 0.0,
        "transactions_analyzed": 0,
    }
    static_result = {
        "noetic": {**static_phase},
        "praxic": {**static_phase},
        "source": "static",
        "reason": "insufficient data",
    }

    try:
        cursor = db.conn.cursor()
        result = {"source": "dynamic", "reason": "brier calibration"}

        for phase in ["noetic", "praxic"]:
            # Get recent trajectory points with both self-assessed and grounded
            cursor.execute("""
                SELECT self_assessed, grounded
                FROM calibration_trajectory
                WHERE ai_id = ? AND phase = ? AND grounded IS NOT NULL
                ORDER BY timestamp DESC
                LIMIT ?
            """, (ai_id, phase, lookback))

            rows = cursor.fetchall()

            if len(rows) < min_transactions:
                result[phase] = {**static_phase, "transactions_analyzed": len(rows)}
                continue

            # Build prediction pairs: (self_assessed, grounded)
            predictions = [(row[0], row[1]) for row in rows]

            # Compute Brier decomposition
            decomp = compute_brier_decomposition(predictions)

            # Threshold inflation driven by RELIABILITY component only
            # reliability = 0 → no inflation (well calibrated)
            # reliability > 0 → inflate proportionally, capped at MAX_INFLATION
            # Scale factor: reliability is typically 0.0-0.25 range,
            # we want MAX_INFLATION at high reliability (~0.15+)
            inflation = min(decomp.reliability * (MAX_INFLATION / 0.15), MAX_INFLATION)

            # Apply inflation: raise know threshold, lower uncertainty tolerance
            know_adjusted = know_base + inflation
            unc_adjusted = unc_base - inflation

            # Clamp to safety ceilings
            know_adjusted = min(SAFETY_CEILINGS["ready_know_threshold"], know_adjusted)
            unc_adjusted = max(SAFETY_CEILINGS["ready_uncertainty_threshold"], unc_adjusted)

            result[phase] = {
                "ready_know_threshold": round(know_adjusted, 3),
                "ready_uncertainty_threshold": round(unc_adjusted, 3),
                "brier_score": decomp.brier_score,
                "brier_reliability": decomp.reliability,
                "brier_resolution": decomp.resolution,
                "brier_uncertainty": decomp.uncertainty,
                "threshold_inflation": round(inflation, 3),
                "transactions_analyzed": decomp.n_predictions,
            }

        # If both phases are still static, mark overall as static
        noetic_static = result.get("noetic", {}).get("brier_score") is None
        praxic_static = result.get("praxic", {}).get("brier_score") is None
        if noetic_static and praxic_static:
            return static_result

        return result

    except Exception as e:
        logger.debug(f"Dynamic threshold computation failed (non-fatal): {e}")
        return static_result


def get_brier_profile(
    ai_id: str,
    db,
    lookback: int = 50,
) -> Dict:
    """Get Brier score profile for an AI across phases.

    Useful for calibration-report and epistemic profiles.
    Returns decomposition per phase with trend information.
    """
    try:
        cursor = db.conn.cursor()
        profile = {}

        for phase in ["noetic", "praxic", "combined"]:
            phase_filter = "AND phase = ?" if phase != "combined" else ""
            params = [ai_id] + ([phase] if phase != "combined" else []) + [lookback]

            cursor.execute(f"""
                SELECT self_assessed, grounded
                FROM calibration_trajectory
                WHERE ai_id = ? {phase_filter} AND grounded IS NOT NULL
                ORDER BY timestamp DESC
                LIMIT ?
            """, params)

            rows = cursor.fetchall()
            if len(rows) < 3:
                profile[phase] = {"status": "insufficient_data", "n": len(rows)}
                continue

            predictions = [(row[0], row[1]) for row in rows]
            decomp = compute_brier_decomposition(predictions)

            # Compute recent vs historical for trend
            recent = predictions[:len(predictions) // 2] if len(predictions) >= 6 else predictions
            historical = predictions[len(predictions) // 2:] if len(predictions) >= 6 else []

            trend = "stable"
            if historical:
                recent_brier = compute_brier_score(recent)
                historical_brier = compute_brier_score(historical)
                diff = recent_brier - historical_brier
                if diff < -0.02:
                    trend = "improving"
                elif diff > 0.02:
                    trend = "degrading"

            profile[phase] = {
                "brier_score": decomp.brier_score,
                "reliability": decomp.reliability,
                "resolution": decomp.resolution,
                "uncertainty": decomp.uncertainty,
                "n_predictions": decomp.n_predictions,
                "trend": trend,
            }

        return profile

    except Exception as e:
        logger.debug(f"Brier profile computation failed: {e}")
        return {}
