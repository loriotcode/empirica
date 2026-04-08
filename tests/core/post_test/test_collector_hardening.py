"""Tests for the collector's insufficient-evidence and source-error handling.

These cover the 'fail loudly' layer added by the sentinel measurer remote-ops
design (2026-04-08):
  - EvidenceBundle has sources_empty (ran ok, returned 0 items) and
    source_errors (collector raised; type+message captured)
  - EvidenceProfile has INSUFFICIENT enum value
  - _resolve_profile() returns INSUFFICIENT for empty changed files
  - collect_all() skips profile collectors when profile is INSUFFICIENT
  - collect_all() distinguishes empty from failed sources and captures error info
"""

from __future__ import annotations

import pytest

from empirica.core.post_test.collector import (
    EvidenceBundle,
    EvidenceProfile,
    PostTestCollector,
)


# ---------------------------------------------------------------------------
# EvidenceBundle: new fields (Task 3)
# ---------------------------------------------------------------------------


def test_evidence_bundle_has_sources_empty_field():
    """EvidenceBundle should expose a sources_empty list (default empty)."""
    bundle = EvidenceBundle(session_id="test")
    assert hasattr(bundle, "sources_empty")
    assert bundle.sources_empty == []


def test_evidence_bundle_has_source_errors_field():
    """EvidenceBundle should expose a source_errors dict (default empty)."""
    bundle = EvidenceBundle(session_id="test")
    assert hasattr(bundle, "source_errors")
    assert bundle.source_errors == {}


def test_evidence_bundle_source_errors_accepts_dict_entries():
    """source_errors should accept string key/value entries (type: msg)."""
    bundle = EvidenceBundle(session_id="test")
    bundle.source_errors["foo"] = "RuntimeError: simulated"
    assert bundle.source_errors == {"foo": "RuntimeError: simulated"}
