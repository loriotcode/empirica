#!/usr/bin/env python3
"""
Semantic Index Loader - Per-Project Documentation Index

Loads SEMANTIC_INDEX.yaml from project root with graceful fallback.

Priority:
1. <git-root>/docs/SEMANTIC_INDEX.yaml (standard location)
2. <git-root>/.empirica/SEMANTIC_INDEX.yaml (alternative)
3. None (graceful degradation)

Usage:
    from empirica.config.semantic_index_loader import load_semantic_index

    index = load_semantic_index()
    if index:
        docs = index.get('index', {})

Author: Rovo Dev
Date: 2025-12-19
"""

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


def load_semantic_index(project_root: str | None = None) -> dict[str, Any] | None:
    """
    Load SEMANTIC_INDEX.yaml from project root.

    Args:
        project_root: Project root directory (defaults to git root)

    Returns:
        Semantic index dict or None if not found
    """
    if project_root is None:
        # Use git root
        from empirica.config.path_resolver import get_git_root
        git_root = get_git_root()
        if not git_root:
            logger.debug("Not in git repo, cannot load semantic index")
            return None
        project_root = str(git_root)

    # Try standard location: docs/SEMANTIC_INDEX.yaml
    docs_path = Path(project_root) / 'docs' / 'SEMANTIC_INDEX.yaml'
    if docs_path.exists():
        try:
            with open(docs_path, encoding='utf-8') as f:
                index = yaml.safe_load(f)
            logger.debug(f"[OK] Loaded semantic index from {docs_path}")
            return index or {}
        except Exception as e:
            logger.warning(f"[WARN]  Failed to load semantic index from {docs_path}: {e}")

    # Try alternative location: .empirica/SEMANTIC_INDEX.yaml
    empirica_path = Path(project_root) / '.empirica' / 'SEMANTIC_INDEX.yaml'
    if empirica_path.exists():
        try:
            with open(empirica_path, encoding='utf-8') as f:
                index = yaml.safe_load(f)
            logger.debug(f"[OK] Loaded semantic index from {empirica_path}")
            return index or {}
        except Exception as e:
            logger.warning(f"[WARN]  Failed to load semantic index from {empirica_path}: {e}")

    # Graceful degradation
    logger.debug(f"No semantic index found in {project_root}")
    return None


def get_semantic_index_path(project_root: str | None = None) -> Path | None:
    """
    Get path to SEMANTIC_INDEX.yaml if it exists.

    Args:
        project_root: Project root directory (defaults to git root)

    Returns:
        Path to semantic index or None
    """
    if project_root is None:
        from empirica.config.path_resolver import get_git_root
        git_root = get_git_root()
        if not git_root:
            return None
        project_root = str(git_root)

    # Check both locations
    docs_path = Path(project_root) / 'docs' / 'SEMANTIC_INDEX.yaml'
    if docs_path.exists():
        return docs_path

    empirica_path = Path(project_root) / '.empirica' / 'SEMANTIC_INDEX.yaml'
    if empirica_path.exists():
        return empirica_path

    return None


if __name__ == '__main__':
    # Test/debug mode

    logging.basicConfig(level=logging.DEBUG)

    print("[SEARCH] Semantic Index Loader Debug\n")

    index = load_semantic_index()
    if index:
        print("[OK] Semantic index loaded")
        print(f"   Version: {index.get('version', 'unknown')}")
        print(f"   Docs indexed: {index.get('total_docs_indexed', 0)}")
        print(f"   Index entries: {len(index.get('index', {}))}")
    else:
        print("[FAIL] No semantic index found")

    path = get_semantic_index_path()
    print(f"\n📍 Semantic index path: {path}")
