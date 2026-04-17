"""
Compliance report command — project-wide quality and regulatory snapshot.

Runs all deterministic checks (ruff, radon, pyright, pytest, pip-audit)
and maps results to regulatory frameworks (EU AI Act, GDPR, ISO 42001).

Machine-readable JSON + human-readable summary.
"""

import json
import logging
import subprocess
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Regulatory framework mappings
REGULATORY_MAP: dict[str, dict[str, Any]] = {
    "lint": {
        "check": "Static analysis (ruff)",
        "frameworks": {
            "eu_ai_act": {"article": "Art. 9", "requirement": "Risk management — code quality assurance"},
            "iso_42001": {"clause": "6.1.2", "requirement": "AI risk assessment — source code quality"},
        },
    },
    "complexity": {
        "check": "Cyclomatic complexity (radon)",
        "frameworks": {
            "eu_ai_act": {"article": "Art. 15(1)", "requirement": "Accuracy — maintainable, auditable code"},
            "iso_42001": {"clause": "8.4", "requirement": "AI system development — complexity management"},
        },
    },
    "type_safety": {
        "check": "Type checking (pyright)",
        "frameworks": {
            "eu_ai_act": {"article": "Art. 15(1)", "requirement": "Accuracy — type-safe operations"},
            "iso_42001": {"clause": "8.4", "requirement": "AI system development — correctness guarantees"},
        },
    },
    "tests": {
        "check": "Test suite (pytest)",
        "frameworks": {
            "eu_ai_act": {"article": "Art. 15(3)", "requirement": "Robustness — functional verification"},
            "iso_42001": {"clause": "8.5", "requirement": "AI system testing and validation"},
        },
    },
    "dep_audit": {
        "check": "Dependency audit (pip-audit)",
        "frameworks": {
            "eu_ai_act": {"article": "Art. 15(4)", "requirement": "Cybersecurity — supply chain security"},
            "iso_42001": {"clause": "A.7.5", "requirement": "Third-party components management"},
            "gdpr": {"article": "Art. 32", "requirement": "Security of processing — dependency integrity"},
        },
    },
    "epistemic_audit": {
        "check": "Epistemic transaction trail (empirica)",
        "frameworks": {
            "eu_ai_act": {"article": "Art. 12", "requirement": "Record-keeping — AI decision audit trail"},
            "iso_42001": {"clause": "9.1", "requirement": "Monitoring and measurement"},
            "gdpr": {"article": "Art. 30", "requirement": "Records of processing activities"},
        },
    },
    "calibration": {
        "check": "Grounded calibration (empirica)",
        "frameworks": {
            "eu_ai_act": {"article": "Art. 14", "requirement": "Human oversight — AI self-assessment accuracy"},
            "iso_42001": {"clause": "9.2", "requirement": "Internal audit — calibration verification"},
        },
    },
}


def _run_check(name: str, cmd: list[str], timeout: int = 120) -> dict[str, Any]:
    """Run a single compliance check and return structured result."""
    start = time.time()
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout
        )
        duration = round(time.time() - start, 2)
        return {
            "check": name,
            "passed": result.returncode == 0,
            "returncode": result.returncode,
            "duration_seconds": duration,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
    except FileNotFoundError:
        return {"check": name, "passed": None, "error": "tool not installed", "duration_seconds": 0}
    except subprocess.TimeoutExpired:
        return {"check": name, "passed": False, "error": f"timeout ({timeout}s)", "duration_seconds": timeout}


def _parse_ruff_result(raw: dict[str, Any]) -> dict[str, Any]:
    """Parse ruff check output into structured result."""
    if raw.get("error"):
        return {**raw, "violations": None, "status": "unavailable"}
    passed = raw["passed"]
    violation_count = 0
    if not passed and raw.get("stdout"):
        for line in raw["stdout"].strip().split("\n"):
            if line.startswith("Found ") and " error" in line:
                try:
                    violation_count = int(line.split()[1])
                except (ValueError, IndexError):
                    pass
    return {
        "check": "lint",
        "tool": "ruff",
        "passed": passed,
        "violations": violation_count,
        "status": "pass" if passed else "fail",
        "duration_seconds": raw["duration_seconds"],
    }


def _parse_c901_result(raw: dict[str, Any]) -> dict[str, Any]:
    """Parse ruff C901 output (cyclomatic complexity > 15)."""
    if raw.get("error"):
        return {**raw, "check": "complexity", "violations": None, "status": "unavailable"}
    passed = raw["passed"]
    violation_count = 0
    if not passed:
        stderr = raw.get("stderr") or raw.get("stdout") or ""
        for line in stderr.strip().split("\n"):
            if line.startswith("Found ") and "error" in line:
                try:
                    violation_count = int(line.split()[1])
                except (ValueError, IndexError):
                    pass
    return {
        "check": "complexity",
        "tool": "ruff (C901, threshold 15)",
        "passed": passed,
        "violations": violation_count,
        "status": "pass" if passed else "fail",
        "duration_seconds": raw["duration_seconds"],
    }


def _parse_pyright_result(raw: dict[str, Any]) -> dict[str, Any]:
    """Parse pyright output."""
    if raw.get("error"):
        return {**raw, "errors": None, "status": "unavailable"}
    errors = 0
    warnings = 0
    for line in (raw.get("stdout") or "").strip().split("\n"):
        if "error" in line and "warning" in line:
            parts = line.split(",")
            for part in parts:
                part = part.strip()
                if "error" in part:
                    try:
                        errors = int(part.split()[0])
                    except (ValueError, IndexError):
                        pass
                if "warning" in part:
                    try:
                        warnings = int(part.split()[0])
                    except (ValueError, IndexError):
                        pass
    return {
        "check": "type_safety",
        "tool": "pyright",
        "passed": errors == 0,
        "errors": errors,
        "warnings": warnings,
        "status": "pass" if errors == 0 else "fail",
        "duration_seconds": raw["duration_seconds"],
    }


def _parse_pytest_result(raw: dict[str, Any]) -> dict[str, Any]:
    """Parse pytest output."""
    if raw.get("error"):
        return {**raw, "passed_count": None, "status": "unavailable"}
    passed_count = 0
    failed_count = 0
    skipped_count = 0
    output = (raw.get("stdout") or "") + (raw.get("stderr") or "")
    for line in output.strip().split("\n"):
        if "passed" in line:
            for part in line.split(","):
                part = part.strip()
                if "passed" in part:
                    try:
                        passed_count = int(part.split()[0])
                    except (ValueError, IndexError):
                        pass
                if "failed" in part:
                    try:
                        failed_count = int(part.split()[0])
                    except (ValueError, IndexError):
                        pass
                if "skipped" in part:
                    try:
                        skipped_count = int(part.split()[0])
                    except (ValueError, IndexError):
                        pass
    return {
        "check": "tests",
        "tool": "pytest",
        "passed": raw["passed"],
        "passed_count": passed_count,
        "failed_count": failed_count,
        "skipped_count": skipped_count,
        "status": "pass" if raw["passed"] else "fail",
        "duration_seconds": raw["duration_seconds"],
    }


def _parse_pip_audit_result(raw: dict[str, Any]) -> dict[str, Any]:
    """Parse pip-audit output."""
    if raw.get("error"):
        return {**raw, "vulnerabilities": None, "status": "unavailable"}
    vuln_count = 0
    output = (raw.get("stdout") or "") + (raw.get("stderr") or "")
    for line in output.strip().split("\n"):
        if line.startswith("Found ") and "vulnerabilit" in line:
            try:
                vuln_count = int(line.split()[1])
            except (ValueError, IndexError):
                pass
    return {
        "check": "dep_audit",
        "tool": "pip-audit",
        "passed": vuln_count == 0,
        "vulnerabilities": vuln_count,
        "status": "pass" if vuln_count == 0 else "fail",
        "duration_seconds": raw["duration_seconds"],
    }


def _build_epistemic_audit(project_root: Path) -> dict[str, Any]:
    """Check for epistemic transaction trail."""
    from empirica.config.path_resolver import get_session_db_path
    try:
        db_path = get_session_db_path()
    except Exception:
        db_path = project_root / ".empirica" / "sessions" / "sessions.db"
    if not db_path.exists():
        return {"check": "epistemic_audit", "passed": None, "status": "unavailable", "reason": "no database"}

    import sqlite3
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT COUNT(*) FROM reflexes WHERE phase = 'POSTFLIGHT'")
        postflights = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM project_findings")
        findings = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM decisions")
        decisions = cursor.fetchone()[0]
    except sqlite3.OperationalError:
        conn.close()
        return {"check": "epistemic_audit", "passed": None, "status": "unavailable", "reason": "schema mismatch"}
    conn.close()

    has_trail = postflights > 0 and findings > 0
    return {
        "check": "epistemic_audit",
        "passed": has_trail,
        "postflights": postflights,
        "findings": findings,
        "decisions": decisions,
        "status": "pass" if has_trail else "fail",
    }


def _build_calibration_check(project_root: Path) -> dict[str, Any]:
    """Check grounded calibration data."""
    from empirica.config.path_resolver import get_session_db_path
    try:
        db_path = get_session_db_path()
    except Exception:
        db_path = project_root / ".empirica" / "sessions" / "sessions.db"
    if not db_path.exists():
        return {"check": "calibration", "passed": None, "status": "unavailable"}

    import sqlite3
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT COUNT(*), AVG(overall_calibration_score)
            FROM grounded_verifications
            WHERE compliance_status = 'grounded'
        """)
        row = cursor.fetchone()
        count = row[0] if row else 0
        avg_score = round(row[1], 4) if row and row[1] else None
    except sqlite3.OperationalError:
        conn.close()
        return {"check": "calibration", "passed": None, "status": "unavailable"}
    conn.close()

    return {
        "check": "calibration",
        "passed": count > 0,
        "grounded_verifications": count,
        "avg_calibration_score": avg_score,
        "status": "pass" if count > 0 else "no_data",
    }


def _compute_overall_status(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute overall compliance status."""
    total = len(results)
    passed = sum(1 for r in results if r.get("passed") is True)
    failed = sum(1 for r in results if r.get("passed") is False)
    unavailable = sum(1 for r in results if r.get("passed") is None)

    if failed == 0 and unavailable == 0:
        status = "fully_compliant"
    elif failed == 0:
        status = "compliant_with_gaps"
    else:
        status = "non_compliant"

    return {
        "status": status,
        "checks_total": total,
        "checks_passed": passed,
        "checks_failed": failed,
        "checks_unavailable": unavailable,
        "score": round(passed / max(total - unavailable, 1), 4),
    }


def _add_regulatory_mapping(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Enrich results with regulatory framework mappings."""
    for result in results:
        check_id = result.get("check", "")
        if check_id in REGULATORY_MAP:
            result["regulatory"] = REGULATORY_MAP[check_id]["frameworks"]
    return results


def run_compliance_report(
    project_root: Path | None = None,
    include_tests: bool = False,
    include_dep_audit: bool = False,
) -> dict[str, Any]:
    """Run full compliance report and return structured results."""
    if project_root is None:
        project_root = Path.cwd()

    results: list[dict[str, Any]] = []

    # Always-run checks (fast)
    ruff_raw = _run_check("ruff", ["ruff", "check"], timeout=30)
    results.append(_parse_ruff_result(ruff_raw))

    complexity_raw = _run_check("ruff-c901", ["ruff", "check", "--select", "C901"], timeout=30)
    results.append(_parse_c901_result(complexity_raw))

    pyright_raw = _run_check("pyright", ["pyright", "empirica/"], timeout=120)
    results.append(_parse_pyright_result(pyright_raw))

    # Optional checks (slow)
    if include_tests:
        pytest_raw = _run_check("pytest", ["python3", "-m", "pytest", "tests/", "-q", "--tb=line"], timeout=300)
        results.append(_parse_pytest_result(pytest_raw))

    if include_dep_audit:
        audit_raw = _run_check("pip-audit", ["pip-audit"], timeout=120)
        results.append(_parse_pip_audit_result(audit_raw))

    # Empirica-specific checks (fast, DB queries)
    results.append(_build_epistemic_audit(project_root))
    results.append(_build_calibration_check(project_root))

    # Enrich with regulatory mappings
    results = _add_regulatory_mapping(results)

    overall = _compute_overall_status(results)

    return {
        "report_version": "1.0",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "project_root": str(project_root),
        "overall": overall,
        "checks": results,
        "regulatory_frameworks": ["EU AI Act (2024/1689)", "GDPR (2016/679)", "ISO/IEC 42001:2023"],
    }


def _print_human_report(report: dict[str, Any]) -> None:
    """Print human-readable compliance report."""
    overall = report["overall"]

    status_icon = {"fully_compliant": "PASS", "compliant_with_gaps": "PARTIAL", "non_compliant": "FAIL"}
    icon = status_icon.get(overall["status"], "?")

    print(f"\n{'=' * 60}")
    print(f"EMPIRICA COMPLIANCE REPORT  [{icon}]")
    print(f"{'=' * 60}")
    print(f"  Generated: {report['timestamp']}")
    print(f"  Project:   {report['project_root']}")
    print(f"  Score:     {overall['score']:.0%} ({overall['checks_passed']}/{overall['checks_total']})")
    print()

    for check in report["checks"]:
        status = check.get("status", "?")
        name = check.get("check", "?")
        tool = check.get("tool", "")
        icon_char = "+" if status == "pass" else "-" if status == "fail" else "?"

        detail = ""
        if name == "lint":
            detail = f"  {check.get('violations', '?')} violations"
        elif name == "complexity":
            detail = f"  {check.get('violations', '0')} functions over CC 15"
        elif name == "type_safety":
            detail = f"  {check.get('errors', '?')} errors, {check.get('warnings', '?')} warnings"
        elif name == "tests":
            detail = f"  {check.get('passed_count', '?')} passed, {check.get('failed_count', '?')} failed"
        elif name == "dep_audit":
            detail = f"  {check.get('vulnerabilities', '?')} known CVEs"
        elif name == "epistemic_audit":
            detail = f"  {check.get('postflights', '?')} transactions, {check.get('findings', '?')} findings"
        elif name == "calibration":
            avg = check.get("avg_calibration_score")
            detail = f"  {check.get('grounded_verifications', '?')} verifications" + (f", avg score {avg}" if avg else "")

        duration = check.get("duration_seconds", "")
        duration_str = f" ({duration}s)" if duration else ""

        print(f"  [{icon_char}] {name:<20} {tool:<12} {status:<12}{detail}{duration_str}")

        # Regulatory mapping
        regulatory = check.get("regulatory", {})
        for framework, mapping in regulatory.items():
            ref = mapping.get("article") or mapping.get("clause", "")
            req = mapping.get("requirement", "")
            print(f"       -> {framework}: {ref} — {req}")

    print(f"\n{'=' * 60}")
    print(f"  Frameworks: {', '.join(report['regulatory_frameworks'])}")
    print(f"{'=' * 60}\n")


def handle_compliance_report_command(args) -> None:
    """Handle compliance-report command."""
    include_tests = getattr(args, "tests", False)
    include_dep_audit = getattr(args, "dep_audit", False)
    output_format = getattr(args, "output", "text")

    report = run_compliance_report(
        include_tests=include_tests,
        include_dep_audit=include_dep_audit,
    )

    if output_format == "json":
        print(json.dumps(report, indent=2))
    else:
        _print_human_report(report)
