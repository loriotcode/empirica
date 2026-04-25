"""Tests for empirica.core.security.scope."""

from __future__ import annotations

from empirica.core.security.scope import (
    _extract_pkg_name,
    _normalize,
    get_empirica_managed_packages,
    is_empirica_managed,
)


def test_normalize_basic():
    assert _normalize("HTTPie") == "httpie"
    assert _normalize("My_Package") == "my-package"
    assert _normalize("already-canonical") == "already-canonical"


def test_extract_pkg_name_simple():
    assert _extract_pkg_name("requests") == "requests"


def test_extract_pkg_name_with_version_spec():
    assert _extract_pkg_name("requests>=2.0") == "requests"
    assert _extract_pkg_name("PyYAML==6.0") == "pyyaml"
    assert _extract_pkg_name("urllib3<3.0") == "urllib3"


def test_extract_pkg_name_with_marker():
    assert _extract_pkg_name("requests; python_version >= '3.8'") == "requests"


def test_extract_pkg_name_with_extras():
    assert _extract_pkg_name("flask[async]>=2.0") == "flask"


def test_extract_pkg_name_normalizes():
    assert _extract_pkg_name("My_Package>=1.0") == "my-package"


def test_extract_pkg_name_empty():
    assert _extract_pkg_name("") == ""
    assert _extract_pkg_name("  ") == ""


def test_get_empirica_managed_returns_root():
    """When empirica is installed (it is in this venv), root is present."""
    managed = get_empirica_managed_packages()
    assert "empirica" in managed


def test_get_empirica_managed_includes_known_deps():
    """A few well-known empirica deps should appear in the managed set."""
    managed = get_empirica_managed_packages()
    # These are core empirica deps (from pyproject.toml)
    for dep in ("pydantic", "sqlalchemy", "pyyaml"):
        assert dep in managed, f"{dep} should be in empirica's managed set"


def test_get_empirica_managed_unknown_root_returns_empty():
    managed = get_empirica_managed_packages(root="this-package-does-not-exist-xyz")
    assert managed == set()


def test_is_empirica_managed_with_injected_set():
    managed = {"foo", "bar-baz"}
    assert is_empirica_managed("foo", managed) is True
    assert is_empirica_managed("Bar_Baz", managed) is True  # normalized
    assert is_empirica_managed("not-listed", managed) is False


def test_is_empirica_managed_normalizes_input():
    managed = {"my-pkg"}
    assert is_empirica_managed("My_Pkg", managed) is True
    assert is_empirica_managed("MY-PKG", managed) is True
