import json
import os
from pathlib import Path

from empirica.utils import session_resolver


def test_write_active_transaction_overwrites_existing_file_with_os_replace(monkeypatch, tmp_path):
    """Regression: Windows os.rename() cannot overwrite an existing sentinel file."""
    monkeypatch.setattr(session_resolver, "_get_instance_suffix", lambda: "_win-default")

    replace_calls: list[tuple[str, str]] = []
    original_replace = os.replace

    def tracking_replace(src: str, dst: str) -> None:
        replace_calls.append((src, dst))
        original_replace(src, dst)

    def fail_rename(*_args, **_kwargs):
        raise AssertionError("write_active_transaction should use os.replace, not os.rename")

    monkeypatch.setattr(os, "replace", tracking_replace)
    monkeypatch.setattr(os, "rename", fail_rename)

    tx_path = tmp_path / ".empirica" / "active_transaction_win-default.json"
    tx_path.parent.mkdir(parents=True, exist_ok=True)
    tx_path.write_text('{"transaction_id":"stale","status":"closed"}', encoding="utf-8")

    session_resolver.write_active_transaction(
        transaction_id="new-transaction-id",
        session_id="new-session-id",
        preflight_timestamp=123.0,
        status="open",
        project_path=str(tmp_path),
    )

    written = json.loads(tx_path.read_text(encoding="utf-8"))
    assert written["transaction_id"] == "new-transaction-id"
    assert written["session_id"] == "new-session-id"
    assert written["status"] == "open"
    assert replace_calls
    assert Path(replace_calls[0][1]) == tx_path
