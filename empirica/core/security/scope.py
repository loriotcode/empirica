"""Determine whether a package is part of empirica's managed dependency surface.

Why this matters: pip-audit scans the active Python environment, which
mixes empirica's own dependencies with whatever else the user has installed
in the same venv. The audit report should separate the two so users see:

- "empirica-managed": empirica's responsibility — empirica should ship a fix
- "user-installed":   user's responsibility — outside empirica's surface

We compute the empirica-managed set by walking the installed metadata
graph rooted at empirica itself: empirica's Requires, plus their Requires
transitively. Anything in that set is empirica's surface; anything else
in the venv is user-installed.
"""

from __future__ import annotations

import importlib.metadata as importlib_metadata
import logging
import re

logger = logging.getLogger(__name__)

EMPIRICA_PACKAGE_NAME = "empirica"

_NAME_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)")


def _normalize(name: str) -> str:
    """Canonicalize a Python distribution name (PEP 503)."""
    return name.lower().replace("_", "-")


def _extract_pkg_name(req_str: str) -> str:
    """Extract a normalized package name from a 'requires' string.

    Handles forms like 'pip>=21.0', 'requests; python_version >= 3.8',
    'click [extra]', 'PyYAML==6.0'.
    """
    match = _NAME_RE.match(req_str.strip())
    if not match:
        return ""
    return _normalize(match.group(1))


def get_empirica_managed_packages(root: str = EMPIRICA_PACKAGE_NAME) -> set[str]:
    """Return the set of normalized package names empirica brings in.

    Includes the root (empirica itself) and all transitive Requires
    that are actually installed. Optional dependencies are included
    only when present in the environment — if a marker excluded them
    or they weren't installed, they don't appear here.

    Returns:
        Set of normalized package names. Empty set if empirica isn't
        importable in this environment.
    """
    try:
        root_dist = importlib_metadata.distribution(root)
    except importlib_metadata.PackageNotFoundError:
        logger.debug("Package %s not found in environment", root)
        return set()

    managed: set[str] = {_normalize(root)}
    queue: list[importlib_metadata.Distribution] = [root_dist]
    seen: set[str] = {_normalize(root)}

    while queue:
        dist = queue.pop()
        for req_str in dist.requires or []:
            name = _extract_pkg_name(req_str)
            if not name or name in seen:
                continue
            seen.add(name)
            try:
                child = importlib_metadata.distribution(name)
            except importlib_metadata.PackageNotFoundError:
                # Optional dep not installed in this env; skip.
                continue
            managed.add(name)
            queue.append(child)

    return managed


def is_empirica_managed(package_name: str, managed: set[str] | None = None) -> bool:
    """True if the named package is in empirica's managed surface."""
    if managed is None:
        managed = get_empirica_managed_packages()
    return _normalize(package_name) in managed
