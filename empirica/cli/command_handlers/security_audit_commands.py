"""CLI handler for `empirica security-audit`.

Phase 1: pip-audit + CISA KEV cross-reference. See
empirica/core/security/audit.py for the audit logic and
docs/architecture/SECURITY_AUDIT.md (TODO) for the design.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def handle_security_audit_command(args) -> None:
    """Run security-audit and emit text or JSON report."""
    from empirica.core.security import run_security_audit

    project_root = Path(getattr(args, "project_root", ".") or ".").resolve()
    refresh = bool(getattr(args, "refresh_feeds", False))
    output_format = getattr(args, "output", "text")

    report = run_security_audit(project_root, refresh_feeds=refresh)

    if output_format == "json":
        print(json.dumps(report, indent=2))
        sys.exit(0 if report["passed"] else 1)

    _print_text_report(report)
    sys.exit(0 if report["passed"] else 1)


def _print_text_report(report: dict[str, Any]) -> None:
    """Emit a human-readable security audit report."""
    summary = report.get("summary", {})
    scanned = report.get("scanned", {})
    kev_meta = report.get("kev_metadata", {})

    status = "[PASS]" if report.get("passed") else "[FAIL]"
    print("=" * 60)
    print(f"EMPIRICA SECURITY AUDIT  {status}")
    print("=" * 60)
    print(f"  Tool:      {scanned.get('tool', '?')} ({scanned.get('status', '?')})")
    if scanned.get("dependencies_scanned") is not None:
        print(f"  Scanned:   {scanned['dependencies_scanned']} dependencies")
    if "error" in kev_meta:
        print(f"  KEV:       UNAVAILABLE — {kev_meta['error']}")
    else:
        age = kev_meta.get("cache_age_hours")
        age_str = f"cache {age:.1f}h old" if age is not None else "fresh"
        print(
            f"  KEV:       v{kev_meta.get('catalog_version', '?')} "
            f"({kev_meta.get('total_entries', '?')} entries, {age_str})"
        )
    print(f"  Findings:  {summary.get('total', 0)} total")
    print(f"             - {summary.get('now', 0)} rotate NOW (in CISA KEV)")
    print(f"             - {summary.get('month', 0)} rotate this month (CVE only)")
    print(f"             - {summary.get('monitor', 0)} monitor")
    print(f"             - {summary.get('safe', 0)} safe")
    print(f"  Duration:  {report.get('duration_seconds', 0)}s")
    print()

    findings = report.get("findings", [])
    if not findings:
        print("  No vulnerabilities found.")
    else:
        for priority, label in (
            ("now", "ROTATE NOW (in CISA KEV — actively exploited)"),
            ("month", "ROTATE THIS MONTH (CVE without observed exploitation)"),
            ("monitor", "MONITOR"),
            ("safe", "SAFE"),
        ):
            bucket = [f for f in findings if f.get("rotate_priority") == priority]
            if not bucket:
                continue
            print(f"  {label}")
            print(f"  {'-' * (len(label))}")
            for f in bucket:
                _print_finding(f)
            print()

    print("=" * 60)
    fw = report.get("frameworks", {})
    if fw:
        print("  Regulatory mapping:")
        for k, v in fw.items():
            print(f"    -> {k}: {v}")
    print("=" * 60)


def _print_finding(f: dict[str, Any]) -> None:
    pkg = f"{f.get('package', '?')}=={f.get('version', '?')}"
    cves = ", ".join(f.get("cve_ids", [])) or f.get("vulnerability_id", "?")
    fix_str = ", ".join(f.get("fix_versions", [])) or "(no fix listed)"
    print(f"    {pkg}")
    print(f"      CVE:  {cves}")
    print(f"      Fix:  {fix_str}")
    if f.get("kev_entry"):
        kev = f["kev_entry"]
        print(f"      KEV:  added {kev.get('date_added')}, due {kev.get('due_date')}")
        if kev.get("ransomware_campaign_use"):
            print(f"      RANSOMWARE: {kev['ransomware_campaign_use']}")
    desc = f.get("description", "")
    if desc:
        print(f"      Note: {desc[:120]}{'...' if len(desc) > 120 else ''}")
