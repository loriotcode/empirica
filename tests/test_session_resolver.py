"""
Tests for session resolver (alias support for session IDs)
"""

import pytest

from empirica.utils.session_resolver import get_latest_session_id, is_session_alias, resolve_session_id


def test_is_session_alias():
    """Test alias detection"""
    assert is_session_alias("latest")
    assert is_session_alias("last")
    assert is_session_alias("latest:active")
    assert is_session_alias("latest:claude-code")
    assert is_session_alias("latest:active:claude-code")

    # UUIDs are not aliases
    assert not is_session_alias("88dbf132-cc7c-4a4b-9b59-77df3b13dbd2")
    assert not is_session_alias("88dbf132")


def test_resolve_full_uuid():
    """Test that full UUIDs pass through unchanged"""
    full_uuid = "88dbf132-cc7c-4a4b-9b59-77df3b13dbd2"
    result = resolve_session_id(full_uuid)
    assert result == full_uuid


def test_resolve_partial_uuid():
    """Test partial UUID resolution (requires database with sessions)"""
    # This test requires at least one session in database
    try:
        result = resolve_session_id("88dbf132")
        # Should return full UUID
        assert len(result) == 36
        assert result.startswith("88dbf132")
        assert "-" in result
    except ValueError:
        # No session found - skip test
        pytest.skip("No sessions in database for partial UUID test")


def test_resolve_latest_alias():
    """Test 'latest' alias resolution (requires database with sessions)"""
    try:
        result = resolve_session_id("latest")
        # Should return full UUID
        assert len(result) == 36
        assert "-" in result
    except ValueError:
        # No sessions found
        pytest.skip("No sessions in database for latest alias test")


def test_resolve_last_alias():
    """Test 'last' alias (synonym for latest)"""
    try:
        latest_result = resolve_session_id("latest")
        last_result = resolve_session_id("last")
        # Should resolve to same session
        assert latest_result == last_result
    except ValueError:
        pytest.skip("No sessions in database")


def test_resolve_latest_active():
    """Test 'latest:active' alias"""
    try:
        result = resolve_session_id("latest:active")
        # Should return full UUID of active session
        assert len(result) == 36
        assert "-" in result
    except ValueError:
        # No active sessions found
        pytest.skip("No active sessions in database")


def test_resolve_latest_with_ai_id():
    """Test 'latest:<ai_id>' alias"""
    try:
        result = resolve_session_id("latest:claude-code")
        # Should return full UUID
        assert len(result) == 36
        assert "-" in result
    except ValueError:
        # No sessions for this AI found
        pytest.skip("No claude-code sessions in database")


def test_resolve_compound_alias():
    """Test compound alias 'latest:active:<ai_id>'"""
    try:
        result = resolve_session_id("latest:active:claude-code")
        # Should return full UUID
        assert len(result) == 36
        assert "-" in result
    except ValueError:
        # No active sessions for this AI found
        pytest.skip("No active claude-code sessions in database")


def test_get_latest_session_id():
    """Test convenience function"""
    try:
        result = get_latest_session_id()
        # Should return full UUID
        assert len(result) == 36
        assert "-" in result
    except ValueError:
        pytest.skip("No sessions in database")


def test_get_latest_session_id_with_filters():
    """Test convenience function with filters"""
    try:
        result = get_latest_session_id(ai_id="claude-code", active_only=True)
        # Should return full UUID
        assert len(result) == 36
        assert "-" in result
    except ValueError:
        pytest.skip("No matching sessions in database")


def test_resolve_invalid_alias():
    """Test that invalid aliases raise ValueError"""
    with pytest.raises(ValueError, match="No session found"):
        # Use an AI ID that definitely doesn't exist
        resolve_session_id("latest:nonexistent-ai-xyz-12345")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
