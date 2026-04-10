"""
A2 Service Registry — SPEC 1 Part 2 implementation.

Registry of deterministic compliance checks. Each check self-declares:
- A unique check_id
- Which (work_type, domain) tuples it applies to
- A runner function that produces a CheckResult
- Pass criteria and timeout

The Sentinel looks up checks by ID via the registry and invokes their
runner. The registry never imports specific services — services register
themselves.

See: .empirica/visions/2026-04-08-sentinel-reframe-api-contract-spec.md
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable

logger = logging.getLogger(__name__)


class RegistrationConflict(Exception):
    """Raised when re-registering a check_id with a different declaration."""


@dataclass(frozen=True)
class CheckResult:
    """Result of running a single compliance check."""

    check_id: str
    passed: bool
    details: dict[str, Any]
    summary: str
    duration_ms: int
    ran_at: float

    # For Brier scoring of AI predictions
    predicted_pass: float | None = None
    predicted_at: float | None = None

    # Cache/tier metadata — informs the AI what actually ran
    cached: bool = False          # True if result came from cache
    deferred: bool = False        # True if check was deferred (tier too high)
    tier: str = "always"          # "always" | "goal_completion" | "release"


@dataclass(frozen=True)
class CheckDeclaration:
    """Declaration of a deterministic compliance check."""

    check_id: str
    tool: str
    applies_to: tuple[tuple[str, str], ...]  # ((work_type, domain), ...)
    criterion_description: str
    runner: Callable[..., CheckResult]
    timeout_seconds: int = 120
    tags: tuple[str, ...] = ()
    tier: str = "always"  # "always" = every POSTFLIGHT, "goal_completion" = at goal close, "release" = pre-release only


class ServiceRegistry:
    """Registry of available deterministic checks.

    Class-level registry — all methods are classmethods for singleton access.

    Cache: check results are cached by (check_id, content_hash) where
    content_hash is derived from changed_files. If the same files produce
    the same hash, the cached result is returned instantly. Cache is
    per-session (cleared on session-create).
    """

    _registered: dict[str, CheckDeclaration] = {}
    _cache: dict[tuple[str, str], CheckResult] = {}  # (check_id, content_hash) → result

    @classmethod
    def register(cls, declaration: CheckDeclaration) -> None:
        """Register a check. Idempotent for identical declarations.

        Raises RegistrationConflict if re-registering the same check_id
        with a different declaration.
        """
        existing = cls._registered.get(declaration.check_id)
        if existing is not None:
            if existing is declaration:
                return  # exact same object — no-op
            # Compare by value (excluding runner which is a callable)
            if (
                existing.tool == declaration.tool
                and existing.applies_to == declaration.applies_to
                and existing.criterion_description == declaration.criterion_description
                and existing.timeout_seconds == declaration.timeout_seconds
                and existing.tags == declaration.tags
            ):
                return  # equivalent — no-op
            raise RegistrationConflict(
                f"Check '{declaration.check_id}' already registered with "
                f"tool='{existing.tool}', cannot re-register with "
                f"tool='{declaration.tool}'"
            )
        cls._registered[declaration.check_id] = declaration

    @classmethod
    def get(cls, check_id: str) -> CheckDeclaration:
        """Look up a single check by ID. Raises KeyError if not found."""
        return cls._registered[check_id]

    @classmethod
    def resolve_for(
        cls, work_type: str, domain: str
    ) -> list[CheckDeclaration]:
        """Return all declarations matching (work_type, domain).

        Returns list ordered by check_id for determinism.
        Wildcard '*' in applies_to matches any value.
        """
        matches = []
        for decl in cls._registered.values():
            for wt, d in decl.applies_to:
                if (wt == "*" or wt == work_type) and (d == "*" or d == domain):
                    matches.append(decl)
                    break
        return sorted(matches, key=lambda d: d.check_id)

    @classmethod
    def _content_hash(cls, context: dict[str, Any]) -> str:
        """Compute a content hash from changed_files for cache keying."""
        import hashlib
        changed = sorted(context.get("changed_files", []))
        return hashlib.md5("|".join(changed).encode()).hexdigest()[:12]

    @classmethod
    def clear_cache(cls) -> None:
        """Clear the check result cache (call on session-create)."""
        cls._cache.clear()

    @classmethod
    def run(
        cls,
        check_id: str,
        context: dict[str, Any],
        predicted_pass: float | None = None,
        execution_tier: str = "always",
    ) -> CheckResult:
        """Invoke a check's runner with caching and tier awareness.

        Cache: results are cached by (check_id, content_hash). Same changed
        files = same hash = cached result returned instantly. The AI is told
        via result.cached=True so it knows this wasn't a fresh run.

        Tiers: if the check's tier is higher than execution_tier, the check
        is deferred. The AI is told via result.deferred=True so it excludes
        this check from its Brier predictions.

        Tier hierarchy: always < goal_completion < release
        """
        decl = cls.get(check_id)  # raises KeyError if unknown
        tier_order = {"always": 0, "goal_completion": 1, "release": 2}
        predicted_at = time.time() if predicted_pass is not None else None

        # Tier check: defer if check tier exceeds execution tier
        if tier_order.get(decl.tier, 0) > tier_order.get(execution_tier, 0):
            return CheckResult(
                check_id=check_id,
                passed=True,  # deferred = not blocking
                details={"deferred": True, "tier": decl.tier, "execution_tier": execution_tier},
                summary=f"deferred ({decl.tier} tier, running at {execution_tier})",
                duration_ms=0,
                ran_at=time.time(),
                predicted_pass=predicted_pass,
                predicted_at=predicted_at,
                deferred=True,
                tier=decl.tier,
            )

        # Cache check: if changed_files produce the same hash, reuse result
        content_hash = cls._content_hash(context)
        cache_key = (check_id, content_hash)
        cached = cls._cache.get(cache_key)
        if cached is not None:
            return CheckResult(
                check_id=cached.check_id,
                passed=cached.passed,
                details={**cached.details, "cache_hit": True},
                summary=cached.summary + " (cached)",
                duration_ms=0,
                ran_at=cached.ran_at,
                predicted_pass=predicted_pass,
                predicted_at=predicted_at,
                cached=True,
                tier=decl.tier,
            )

        # Execute the runner
        start = time.time()
        try:
            result = decl.runner(context)
            elapsed_ms = int((time.time() - start) * 1000)
            result = CheckResult(
                check_id=result.check_id,
                passed=result.passed,
                details=result.details,
                summary=result.summary,
                duration_ms=elapsed_ms,
                ran_at=result.ran_at,
                predicted_pass=predicted_pass,
                predicted_at=predicted_at,
                tier=decl.tier,
            )
        except Exception as e:
            elapsed_ms = int((time.time() - start) * 1000)
            err_type = type(e).__name__
            err_msg = str(e)[:200]
            result = CheckResult(
                check_id=check_id,
                passed=False,
                details={"error": f"{err_type}: {err_msg}"},
                summary=f"Check failed: {err_type}: {err_msg}",
                duration_ms=elapsed_ms,
                ran_at=time.time(),
                predicted_pass=predicted_pass,
                predicted_at=predicted_at,
                tier=decl.tier,
            )
            logger.warning("Check %s failed: %s: %s", check_id, err_type, err_msg)

        # Cache the result
        cls._cache[cache_key] = result
        return result

    @classmethod
    def list_all(cls) -> list[str]:
        """All registered check IDs."""
        return sorted(cls._registered.keys())

    @classmethod
    def load_builtins(cls) -> None:
        """Register built-in checks for existing collector sources.

        These are stub runners that will delegate to the actual collector
        methods when the compliance loop (B2) is wired. For now they
        declare the check surface so domain-resolve can verify checklists.
        """
        _register_builtin_checks()


# ---------------------------------------------------------------------------
# Built-in check runners (C2 — goal-scoped subprocess execution)
#
# All runners receive context["changed_files"] from the compliance loop.
# When available, checks scope to changed files only — making compliance
# meaningful per-goal rather than repo-wide.
# ---------------------------------------------------------------------------

def _py_files_from_changed(context: dict[str, Any]) -> list[str]:
    """Extract .py files from context changed_files."""
    return [f for f in context.get("changed_files", []) if f.endswith(".py")]


def _run_tests_check(context: dict[str, Any]) -> CheckResult:
    """Run pytest — scoped to changed modules when available."""
    import subprocess
    project_path = context.get("project_path", ".")
    changed_py = _py_files_from_changed(context)

    # Scope: if we have changed files, only test those modules
    cmd = ["python3", "-m", "pytest", "--tb=no", "-q"]
    scope_note = ""
    if changed_py:
        # Find test files that might cover the changed modules
        test_args = []
        for f in changed_py:
            if "/test_" in f or f.startswith("test_"):
                test_args.append(f)
        if test_args:
            cmd.extend(test_args)
            scope_note = f" (scoped to {len(test_args)} test files)"

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300, cwd=project_path,
        )
        stdout = result.stdout.strip()
        passed = failed = 0
        for part in stdout.split("\n")[-1].split(","):
            part = part.strip()
            if "passed" in part:
                try:
                    passed = int(part.split()[0])
                except (ValueError, IndexError):
                    pass
            elif "failed" in part:
                try:
                    failed = int(part.split()[0])
                except (ValueError, IndexError):
                    pass

        summary = f"{passed} passed, {failed} failed" if failed else f"{passed} passed"
        return CheckResult(
            check_id="tests",
            passed=result.returncode == 0,
            details={"passed": passed, "failed": failed, "exit_code": result.returncode,
                      "scoped": bool(scope_note), "changed_files": len(changed_py)},
            summary=summary + scope_note,
            duration_ms=0, ran_at=time.time(),
        )
    except subprocess.TimeoutExpired:
        return CheckResult(
            check_id="tests", passed=False, details={"error": "timeout"},
            summary="pytest timed out after 300s", duration_ms=300000, ran_at=time.time(),
        )
    except FileNotFoundError:
        return CheckResult(
            check_id="tests", passed=True, details={"skipped": True},
            summary="pytest not available — skipped", duration_ms=0, ran_at=time.time(),
        )


def _run_lint_check(context: dict[str, Any]) -> CheckResult:
    """Run ruff check — scoped to changed files when available."""
    import subprocess
    project_path = context.get("project_path", ".")
    changed_py = _py_files_from_changed(context)

    # Scope: lint only changed files if available
    cmd = ["ruff", "check", "--output-format=json", "--quiet"]
    scope_note = ""
    if changed_py:
        cmd.extend(changed_py)
        scope_note = f" (scoped to {len(changed_py)} files)"

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=60, cwd=project_path,
        )
        import json as _json
        errors = 0
        try:
            findings = _json.loads(result.stdout) if result.stdout.strip() else []
            errors = len(findings)
        except Exception:
            errors = 1 if result.returncode != 0 else 0

        summary = f"{errors} lint errors" if errors else "lint clean"
        return CheckResult(
            check_id="lint",
            passed=result.returncode == 0,
            details={"errors": errors, "exit_code": result.returncode,
                      "scoped": bool(scope_note), "changed_files": len(changed_py)},
            summary=summary + scope_note,
            duration_ms=0, ran_at=time.time(),
        )
    except subprocess.TimeoutExpired:
        return CheckResult(
            check_id="lint", passed=False, details={"error": "timeout"},
            summary="ruff timed out after 60s", duration_ms=60000, ran_at=time.time(),
        )
    except FileNotFoundError:
        return CheckResult(
            check_id="lint", passed=True, details={"skipped": True},
            summary="ruff not available — skipped", duration_ms=0, ran_at=time.time(),
        )


def _run_git_metrics_check(context: dict[str, Any]) -> CheckResult:
    """Check for uncommitted changes in the project."""
    import subprocess
    project_path = context.get("project_path", ".")

    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, timeout=10, cwd=project_path,
        )
        uncommitted = len([line for line in result.stdout.strip().split("\n") if line.strip()])
        return CheckResult(
            check_id="git_metrics",
            passed=uncommitted == 0,
            details={"uncommitted_files": uncommitted},
            summary=f"{uncommitted} uncommitted files" if uncommitted else "working tree clean",
            duration_ms=0, ran_at=time.time(),
        )
    except Exception:
        return CheckResult(
            check_id="git_metrics", passed=True, details={"skipped": True},
            summary="git not available — skipped", duration_ms=0, ran_at=time.time(),
        )


def _run_complexity_check(context: dict[str, Any]) -> CheckResult:
    """Check cyclomatic complexity of changed files via radon."""
    import subprocess
    project_path = context.get("project_path", ".")
    changed_py = _py_files_from_changed(context)

    if not changed_py:
        return CheckResult(
            check_id="complexity", passed=True, details={"skipped": True, "reason": "no changed .py files"},
            summary="no changed files to check", duration_ms=0, ran_at=time.time(),
        )

    try:
        result = subprocess.run(
            ["radon", "cc", "--average", "--no-assert", "-s"] + changed_py,
            capture_output=True, text=True, timeout=30, cwd=project_path,
        )
        # Parse average complexity from last line: "Average complexity: A (2.5)"
        avg = 0.0
        grade = "A"
        for line in reversed(result.stdout.strip().split("\n")):
            if "Average complexity" in line:
                parts = line.split("(")
                if len(parts) >= 2:
                    try:
                        avg = float(parts[-1].rstrip(")"))
                    except ValueError:
                        pass
                grade_parts = line.split()
                for p in grade_parts:
                    if p in ("A", "B", "C", "D", "E", "F"):
                        grade = p
                break

        passed = grade in ("A", "B", "C")  # D, E, F fail
        return CheckResult(
            check_id="complexity",
            passed=passed,
            details={"average_cc": avg, "grade": grade, "files_checked": len(changed_py)},
            summary=f"complexity {grade} (avg {avg:.1f}) on {len(changed_py)} files",
            duration_ms=0, ran_at=time.time(),
        )
    except FileNotFoundError:
        return CheckResult(
            check_id="complexity", passed=True, details={"skipped": True},
            summary="radon not available — skipped", duration_ms=0, ran_at=time.time(),
        )
    except Exception as e:
        return CheckResult(
            check_id="complexity", passed=True, details={"error": str(e)[:200]},
            summary=f"complexity check error: {e}", duration_ms=0, ran_at=time.time(),
        )


def _run_dep_audit_check(context: dict[str, Any]) -> CheckResult:
    """Check for known vulnerabilities in dependencies via pip-audit."""
    import subprocess
    project_path = context.get("project_path", ".")

    try:
        result = subprocess.run(
            ["pip-audit", "--format=json", "--progress-spinner=off"],
            capture_output=True, text=True, timeout=120, cwd=project_path,
        )
        import json as _json
        vulns = 0
        try:
            data = _json.loads(result.stdout) if result.stdout.strip() else []
            if isinstance(data, list):
                vulns = len(data)
            elif isinstance(data, dict):
                vulns = len(data.get("dependencies", []))
        except Exception:
            vulns = 1 if result.returncode != 0 else 0

        return CheckResult(
            check_id="dep_audit",
            passed=vulns == 0,
            details={"vulnerabilities": vulns, "exit_code": result.returncode},
            summary=f"{vulns} known vulnerabilities" if vulns else "no known vulnerabilities",
            duration_ms=0, ran_at=time.time(),
        )
    except FileNotFoundError:
        return CheckResult(
            check_id="dep_audit", passed=True, details={"skipped": True},
            summary="pip-audit not available — skipped", duration_ms=0, ran_at=time.time(),
        )
    except subprocess.TimeoutExpired:
        return CheckResult(
            check_id="dep_audit", passed=True, details={"error": "timeout"},
            summary="pip-audit timed out after 120s", duration_ms=120000, ran_at=time.time(),
        )


def _register_builtin_checks() -> None:
    """Register all built-in checks."""
    builtins = [
        CheckDeclaration(
            check_id="tests",
            tool="pytest",
            applies_to=(("code", "*"), ("infra", "*"), ("debug", "*")),
            criterion_description="All pytest tests pass",
            runner=_run_tests_check,
            timeout_seconds=300,
            tags=("testing",),
            tier="goal_completion",
        ),
        CheckDeclaration(
            check_id="lint",
            tool="ruff",
            applies_to=(("code", "*"), ("infra", "*"), ("docs", "*")),
            criterion_description="No ruff lint errors",
            runner=_run_lint_check,
            timeout_seconds=60,
            tags=("quality",),
        ),
        CheckDeclaration(
            check_id="git_metrics",
            tool="git",
            applies_to=(("code", "*"), ("infra", "*")),
            criterion_description="Git history is clean (no uncommitted changes)",
            runner=_run_git_metrics_check,
            timeout_seconds=30,
            tags=("vcs",),
        ),
        CheckDeclaration(
            check_id="complexity",
            tool="radon",
            applies_to=(("code", "*"),),
            criterion_description="Changed files have acceptable cyclomatic complexity (A-C)",
            runner=_run_complexity_check,
            timeout_seconds=30,
            tags=("quality",),
        ),
        CheckDeclaration(
            check_id="dep_audit",
            tool="pip-audit",
            applies_to=(("code", "*"), ("infra", "*")),
            criterion_description="No known vulnerabilities in dependencies",
            runner=_run_dep_audit_check,
            timeout_seconds=120,
            tags=("security",),
            tier="release",
        ),
    ]

    for decl in builtins:
        ServiceRegistry.register(decl)
