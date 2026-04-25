"""Security audit module — supply-chain + credential rotation tracking.

Phase 1 (current):
    - CISA KEV feed download + cache
    - pip-audit cross-reference for actively-exploited CVEs
    - rotate-priority report (now / month / monitor / safe)

Phase 2+ (future): OSV direct multi-ecosystem, deps.dev OpenSSF Scorecards,
local credential enumeration.
"""

from .audit import run_security_audit
from .kev_feed import KEVFeed

__all__ = ["KEVFeed", "run_security_audit"]
