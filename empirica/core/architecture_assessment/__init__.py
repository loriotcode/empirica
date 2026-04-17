"""
Epistemic Architecture Assessment

Applies Empirica's epistemic framework to code architecture decisions.
Meta-analysis: using Empirica to assess Empirica.

Core insight: The same vectors that track AI epistemic state can track
code component health. High uncertainty about a component = high risk.

Components:
- ComponentAssessor: Main orchestrator combining all analyzers
- CouplingAnalyzer: Dependency graph, API surface, boundary clarity
- StabilityEstimator: Git history, change velocity, ownership patterns

Output: ComponentAssessment with vectors mapped to architecture concerns.

Usage:
    from empirica.core.architecture_assessment import ComponentAssessor

    assessor = ComponentAssessor("/path/to/project")
    assessment = assessor.assess("src/module.py")
    print(assessment.summary())
"""

from .assessor import ComponentAssessor
from .coupling_analyzer import CouplingAnalyzer
from .schema import (
    ArchitectureVectors,
    ComponentAssessment,
    CouplingMetrics,
    StabilityMetrics,
)
from .stability_estimator import StabilityEstimator

__all__ = [
    "ArchitectureVectors",
    "ComponentAssessment",
    "ComponentAssessor",
    "CouplingAnalyzer",
    "CouplingMetrics",
    "StabilityEstimator",
    "StabilityMetrics",
]
