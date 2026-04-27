"""Security audit module -- supply-chain + credential rotation tracking.

Phase 1 (current):
    - CISA KEV feed download + cache
    - pip-audit cross-reference for actively-exploited CVEs
    - rotate-priority report (now / month / monitor / safe)

Phase 2+ (future): OSV direct multi-ecosystem, deps.dev OpenSSF Scorecards,
local credential enumeration.
"""

from .audit import run_security_audit
from .kev_feed import KEVFeed
from .scope import get_empirica_managed_packages, is_empirica_managed

__all__ = [
    "KEVFeed",
    "get_empirica_managed_packages",
    "is_empirica_managed",
    "run_security_audit",
]
