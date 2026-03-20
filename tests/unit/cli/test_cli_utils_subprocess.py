import subprocess

from empirica.cli.cli_utils import run_empirica_subprocess


def test_run_empirica_subprocess_forces_utf8(monkeypatch):
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(command, 0, stdout="{}", stderr="")

    monkeypatch.setattr("empirica.cli.cli_utils.subprocess.run", fake_run)

    result = run_empirica_subprocess(
        ["empirica", "project-bootstrap", "--output", "json"],
        cwd="C:/repo",
        timeout=30,
    )

    assert result.returncode == 0
    assert captured["command"] == ["empirica", "project-bootstrap", "--output", "json"]
    assert captured["kwargs"]["cwd"] == "C:/repo"
    assert captured["kwargs"]["timeout"] == 30
    assert captured["kwargs"]["capture_output"] is True
    assert captured["kwargs"]["text"] is True
    assert captured["kwargs"]["encoding"] == "utf-8"
    assert captured["kwargs"]["errors"] == "replace"
    assert captured["kwargs"]["env"]["PYTHONIOENCODING"] == "utf-8"
    assert captured["kwargs"]["env"]["PYTHONUTF8"] == "1"
