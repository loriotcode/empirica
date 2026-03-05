"""
Post-Test Verification System

Grounds epistemic calibration in objective evidence rather than
self-referential PREFLIGHT-to-POSTFLIGHT deltas.

Modules:
    collector - PostTestCollector: gathers deterministic evidence
    prose_collector - ProseEvidenceCollector: non-code evidence (textstat, proselint, vale)
    web_collector - WebEvidenceCollector: web/static-site evidence (build, HTML, links, assets)
    mapper - EvidenceMapper: maps evidence to vector estimates
    grounded_calibration - GroundedCalibrationManager: parallel Bayesian track
    trajectory_tracker - TrajectoryTracker: POSTFLIGHT-to-POSTFLIGHT evolution

Evidence profiles (set via project.yaml, --evidence-profile, or EMPIRICA_EVIDENCE_PROFILE):
    code - ruff, radon, pyright, pytest, git (default for code repos)
    prose - textstat, proselint, vale, document metrics, source quality
    web - build verification, HTML validation, link integrity, terminology, assets
    hybrid - all evidence sources (code + prose + web when detected)
    auto - detect from session content
"""
