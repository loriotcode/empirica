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


class ServiceRegistry:
    """Registry of available deterministic checks.

    Class-level registry — all methods are classmethods for singleton access.
    """

    _registered: dict[str, CheckDeclaration] = {}

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
    def run(
        cls,
        check_id: str,
        context: dict[str, Any],
        predicted_pass: float | None = None,
    ) -> CheckResult:
        """Invoke a check's runner. Handles exceptions gracefully.

        If the runner raises, returns a failed CheckResult with the
        exception details. Duration is always measured.
        """
        decl = cls.get(check_id)  # raises KeyError if unknown
        start = time.time()
        predicted_at = time.time() if predicted_pass is not None else None

        try:
            result = decl.runner(context)
            elapsed_ms = int((time.time() - start) * 1000)
            # Ensure duration reflects actual measurement
            result = CheckResult(
                check_id=result.check_id,
                passed=result.passed,
                details=result.details,
                summary=result.summary,
                duration_ms=elapsed_ms,
                ran_at=result.ran_at,
                predicted_pass=predicted_pass,
                predicted_at=predicted_at,
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
            )
            logger.warning("Check %s failed: %s: %s", check_id, err_type, err_msg)

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
# Built-in check runners (stubs — B2 will wire real execution)
# ---------------------------------------------------------------------------

def _run_tests_check(context: dict[str, Any]) -> CheckResult:
    """Stub: delegates to pytest when B2 compliance loop is active."""
    return CheckResult(
        check_id="tests",
        passed=True,
        details={"stub": True, "note": "Stub runner — B2 will wire real pytest execution"},
        summary="tests check (stub — not yet wired to pytest)",
        duration_ms=0,
        ran_at=time.time(),
    )


def _run_lint_check(context: dict[str, Any]) -> CheckResult:
    """Stub: delegates to ruff when B2 compliance loop is active."""
    return CheckResult(
        check_id="lint",
        passed=True,
        details={"stub": True, "note": "Stub runner — B2 will wire real ruff execution"},
        summary="lint check (stub — not yet wired to ruff)",
        duration_ms=0,
        ran_at=time.time(),
    )


def _run_git_metrics_check(context: dict[str, Any]) -> CheckResult:
    """Stub: delegates to git metrics collector."""
    return CheckResult(
        check_id="git_metrics",
        passed=True,
        details={"stub": True},
        summary="git metrics check (stub)",
        duration_ms=0,
        ran_at=time.time(),
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
    ]

    for decl in builtins:
        ServiceRegistry.register(decl)
