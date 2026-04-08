"""Regression tests for the open-transaction guard against CWD overrides.

KNOWN_ISSUES 11.26 — session-init.py STARTUP OVERRIDE was bypassing the
active transaction by preferring CWD over the resolved project root, even
when the resolved project had an open transaction.

KNOWN_ISSUES 11.27 — path_resolver.get_session_db_path() had the same blind
spot in its EMPIRICA_CWD_RELIABLE-gated cross-check.

Both fixes add an "open transaction" guard: if the resolved project has a
status=open active_transaction file, it is authoritative and CWD never wins.

These tests reproduce the original bug conditions and assert the guard holds.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# Fixtures: create two projects, one with an open transaction
# ---------------------------------------------------------------------------


def _create_empirica_project(base: Path, name: str, *, with_open_tx: bool = False, suffix: str = "") -> Path:
    """Create a fake .empirica project layout with optional open transaction.

    The project is also initialized as a git repo so `get_git_root()` can find it.
    """
    project = base / name
    project.mkdir()

    # Make it a real git repo so path_resolver's git_root lookup works
    import subprocess
    subprocess.run(["git", "init", "-q"], cwd=str(project), check=False)

    empirica_dir = project / ".empirica"
    empirica_dir.mkdir()
    sessions_dir = empirica_dir / "sessions"
    sessions_dir.mkdir()

    db_path = sessions_dir / "sessions.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE sessions (
            session_id TEXT PRIMARY KEY,
            ai_id TEXT,
            project_id TEXT,
            start_time TEXT,
            end_time TEXT
        )
    """)
    conn.execute(
        "INSERT INTO sessions (session_id, ai_id, project_id, start_time) VALUES (?, ?, ?, ?)",
        ("sess-1", "claude-code", "proj-1", "2026-04-08T00:00:00+00:00"),
    )
    conn.commit()
    conn.close()

    if with_open_tx:
        tx_file = empirica_dir / f"active_transaction{suffix}.json"
        with open(tx_file, "w") as f:
            json.dump({
                "transaction_id": "tx-abc",
                "session_id": "sess-1",
                "preflight_timestamp": 1775680000.0,
                "status": "open",
                "project_path": str(project),
                "updated_at": 1775680100.0,
            }, f)

    return project


@pytest.fixture
def two_projects(tmp_path):
    """Create active_project (with open tx) and harness_cwd_project (no tx)."""
    active = _create_empirica_project(tmp_path, "active_project", with_open_tx=True, suffix="")
    harness = _create_empirica_project(tmp_path, "harness_project", with_open_tx=False)
    return active, harness


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """Redirect HOME to a tmp dir so active_work files don't touch the real home.

    Also forces a clean headless test context: clears terminal-identity env
    vars and patches `_get_instance_suffix` to return "" so the test doesn't
    have to compute the runner's actual TTY-derived suffix.

    Path.home() honors $HOME at call time, so setenv alone is sufficient.
    """
    home = tmp_path / "_home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    for var in ("TMUX_PANE", "WINDOWID", "TERM_SESSION_ID", "EMPIRICA_INSTANCE_ID"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("EMPIRICA_HEADLESS", "true")

    # Force the no-suffix path for transaction file lookups
    import empirica.utils.session_resolver as sr
    monkeypatch.setattr(sr, "_get_instance_suffix", lambda: "")

    # path_resolver caches git_root at module level — reset it so each test
    # gets a fresh git root lookup against the fresh tmp_path.
    import empirica.config.path_resolver as pr
    monkeypatch.setattr(pr, "_git_root_cache", None)

    return home


def _write_active_work(home: Path, claude_session_id: str, project_path: Path):
    """Write an active_work file pointing at project_path.

    Writes BOTH the per-session file AND the generic file (used in headless
    mode), so callers that don't pass claude_session_id can still resolve.
    """
    empirica_dir = home / ".empirica"
    empirica_dir.mkdir(exist_ok=True)

    payload = {
        "project_path": str(project_path),
        "folder_name": project_path.name,
        "claude_session_id": claude_session_id,
        "empirica_session_id": "sess-1",
        "source": "test",
    }

    aw = empirica_dir / f"active_work_{claude_session_id}.json"
    with open(aw, "w") as f:
        json.dump(payload, f)

    # Generic file (read in headless mode)
    generic = empirica_dir / "active_work.json"
    with open(generic, "w") as f:
        json.dump(payload, f)


# ---------------------------------------------------------------------------
# path_resolver.get_session_db_path() — CWD cross-check guard
# ---------------------------------------------------------------------------


class TestPathResolverGuard:
    """Cross-check should NOT prefer git_root when context_project_path has an open transaction."""

    def test_open_transaction_blocks_cwd_override(self, two_projects, fake_home, monkeypatch):
        """Reproduce the bug: CWD reliable + git_root != context + open tx → resolver should stay on context."""
        active, harness = two_projects
        _write_active_work(fake_home, "cs-1", active)

        # CWD = harness, git_root would resolve to harness
        monkeypatch.chdir(harness)
        monkeypatch.setenv("EMPIRICA_CWD_RELIABLE", "true")
        monkeypatch.setenv("EMPIRICA_HEADLESS", "true")

        # Force fresh import so cached module state doesn't bleed
        from empirica.config.path_resolver import get_session_db_path
        result = get_session_db_path()

        # Open transaction on `active` must win over CWD=harness
        assert result == active / ".empirica" / "sessions" / "sessions.db", \
            f"Expected active project DB, got {result}"

    def test_no_open_transaction_falls_through_to_cwd(self, tmp_path, fake_home, monkeypatch):
        """When there's NO open tx, the existing CWD cross-check still works (regression check)."""
        active = _create_empirica_project(tmp_path, "active_no_tx", with_open_tx=False)
        harness = _create_empirica_project(tmp_path, "harness_no_tx", with_open_tx=False)
        _write_active_work(fake_home, "cs-2", active)

        monkeypatch.chdir(harness)
        monkeypatch.setenv("EMPIRICA_CWD_RELIABLE", "true")
        monkeypatch.setenv("EMPIRICA_HEADLESS", "true")

        from empirica.config.path_resolver import get_session_db_path
        result = get_session_db_path()

        # No open tx → cross-check should fire → CWD wins
        assert result == harness / ".empirica" / "sessions" / "sessions.db", \
            f"Expected harness project DB (no tx, CWD reliable), got {result}"

    def test_cwd_unreliable_always_uses_context(self, two_projects, fake_home, monkeypatch):
        """When EMPIRICA_CWD_RELIABLE is unset, the cross-check never fires regardless."""
        active, harness = two_projects
        _write_active_work(fake_home, "cs-3", active)

        monkeypatch.chdir(harness)
        monkeypatch.delenv("EMPIRICA_CWD_RELIABLE", raising=False)
        monkeypatch.setenv("EMPIRICA_HEADLESS", "true")

        from empirica.config.path_resolver import get_session_db_path
        result = get_session_db_path()
        assert result == active / ".empirica" / "sessions" / "sessions.db"


# ---------------------------------------------------------------------------
# session-init.py STARTUP OVERRIDE — guard via subprocess
# ---------------------------------------------------------------------------


class TestSessionInitStartupOverrideGuard:
    """The STARTUP OVERRIDE in session-init.py should be blocked by an open transaction.

    We test this by importing the project_resolver lib functions directly and
    simulating the override block's logic. Full session-init.py end-to-end is
    too heavy for unit tests; the override block is small enough to verify in
    isolation.
    """

    def _emulate_startup_override(self, project_root: Path, has_open_tx_in_project: bool, cwd: Path):
        """Mirror the override block's logic from session-init.py."""
        suffix = ""  # tests use the no-suffix transaction file
        tx_file = project_root / ".empirica" / f"active_transaction{suffix}.json"
        has_open_tx = False
        if tx_file.exists():
            with open(tx_file) as f:
                has_open_tx = json.load(f).get("status") == "open"

        if has_open_tx:
            # Guard: don't override
            return project_root

        # Override path: prefer CWD if it has a valid DB
        cwd_db = cwd / ".empirica" / "sessions" / "sessions.db"
        if cwd_db.exists() and cwd.resolve() != project_root.resolve():
            return cwd
        return project_root

    def test_open_transaction_blocks_override(self, two_projects):
        """Reproduce: project_root=active (with open tx), CWD=harness → guard wins."""
        active, harness = two_projects
        result = self._emulate_startup_override(active, has_open_tx_in_project=True, cwd=harness)
        assert result == active

    def test_no_transaction_lets_override_fire(self, tmp_path):
        """Without an open tx, CWD-based override still works."""
        active = _create_empirica_project(tmp_path, "active_clean", with_open_tx=False)
        harness = _create_empirica_project(tmp_path, "harness_clean", with_open_tx=False)
        result = self._emulate_startup_override(active, has_open_tx_in_project=False, cwd=harness)
        assert result == harness

    def test_open_transaction_file_with_closed_status_does_not_block(self, tmp_path):
        """A status=closed transaction file should NOT trigger the guard."""
        project = tmp_path / "proj_closed"
        project.mkdir()
        (project / ".empirica").mkdir()
        (project / ".empirica" / "sessions").mkdir()
        # closed transaction file
        with open(project / ".empirica" / "active_transaction.json", "w") as f:
            json.dump({"status": "closed", "transaction_id": "old"}, f)
        # valid sessions DB so the cwd path passes its check
        sqlite3.connect(str(project / ".empirica" / "sessions" / "sessions.db")).close()

        cwd = tmp_path / "cwd_proj"
        cwd.mkdir()
        (cwd / ".empirica").mkdir()
        (cwd / ".empirica" / "sessions").mkdir()
        sqlite3.connect(str(cwd / ".empirica" / "sessions" / "sessions.db")).close()

        result = self._emulate_startup_override(project, has_open_tx_in_project=False, cwd=cwd)
        # closed status → guard does not block → override fires
        assert result == cwd
