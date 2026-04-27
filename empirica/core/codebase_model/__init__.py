"""
Codebase Model -- Temporal Entity Tracking for Empirica

Tracks codebase entities (functions, classes, APIs, files) with temporal
validity, evidence chains, and relationship graphs. Inspired by world-model-mcp
(MIT, forked at github.com/Nubaeon/world-model-mcp), adapted to Empirica's
architecture.

Key concepts:
- Entity: A resolved identity in the codebase (function, class, API, file)
- Fact: A temporal assertion with validAt/invalidAt and evidence chain
- Constraint: A learned pattern from corrections (extends Empirica lessons)
- Relationship: Directional link between entities (calls, imports, depends_on)

Integration points:
- PostToolUse hook feeds entity extraction from file edits
- CHECK enrichment queries entity graph for context
- Grounded calibration uses entity metrics as evidence source
"""
