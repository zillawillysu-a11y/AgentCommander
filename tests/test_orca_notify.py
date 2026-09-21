import json
import os
from pathlib import Path

from commander import orca_notify


def test_inherited_orca_terminal_is_preferred(monkeypatch, tmp_path):
    handle = "term_12345678-1234-1234-1234-123456789abc"
    monkeypatch.setenv("ORCA_TERMINAL_HANDLE", handle)
    assert orca_notify.capture_notification_terminal(tmp_path) == handle


def test_unique_connected_codex_terminal_is_discovered(monkeypatch, tmp_path):
    monkeypatch.delenv("ORCA_TERMINAL_HANDLE", raising=False)
    monkeypatch.setattr(orca_notify, "_orca_executable", lambda: "orca")
    payload = {"result": {"terminals": [
        {"handle": "term_12345678-1234-1234-1234-123456789abc", "agentIdentity": "codex", "connected": True, "writable": True, "orphaned": False},
        {"handle": "term_abcdefab-1234-1234-1234-abcdefabcdef", "connected": True, "writable": True},
    ]}}
    class Completed:
        returncode = 0
        stdout = json.dumps(payload)
        stderr = ""
    monkeypatch.setattr(orca_notify.subprocess, "run", lambda *args, **kwargs: Completed())
    assert orca_notify.capture_notification_terminal(tmp_path) == "term_12345678-1234-1234-1234-123456789abc"


def test_most_recent_active_codex_terminal_is_selected(monkeypatch, tmp_path):
    monkeypatch.delenv("ORCA_TERMINAL_HANDLE", raising=False)
    monkeypatch.setattr(orca_notify, "_orca_executable", lambda: "orca")
    monkeypatch.setattr(orca_notify.time, "time", lambda: 1000.0)
    payload = {"result": {"terminals": [
        {"handle": "term_12345678-1234-1234-1234-123456789abc", "agentIdentity": "codex", "connected": True, "writable": True, "orphaned": False, "lastOutputAt": 900_000},
        {"handle": "term_abcdefab-1234-1234-1234-abcdefabcdef", "agentIdentity": "codex", "connected": True, "writable": True, "orphaned": False, "lastOutputAt": 999_000},
    ]}}
    class Completed:
        returncode = 0
        stdout = json.dumps(payload)
        stderr = ""
    monkeypatch.setattr(orca_notify.subprocess, "run", lambda *args, **kwargs: Completed())
    assert orca_notify.capture_notification_terminal(tmp_path) == "term_abcdefab-1234-1234-1234-abcdefabcdef"


def test_ambiguous_stale_codex_terminals_are_not_guessed(monkeypatch, tmp_path):
    monkeypatch.delenv("ORCA_TERMINAL_HANDLE", raising=False)
    monkeypatch.setattr(orca_notify, "_orca_executable", lambda: "orca")
    monkeypatch.setattr(orca_notify.time, "time", lambda: 1000.0)
    payload = {"result": {"terminals": [
        {"handle": "term_12345678-1234-1234-1234-123456789abc", "agentIdentity": "codex", "connected": True, "writable": True, "orphaned": False, "lastOutputAt": 100_000},
        {"handle": "term_abcdefab-1234-1234-1234-abcdefabcdef", "agentIdentity": "codex", "connected": True, "writable": True, "orphaned": False, "lastOutputAt": 90_000},
    ]}}
    class Completed:
        returncode = 0
        stdout = json.dumps(payload)
        stderr = ""
    monkeypatch.setattr(orca_notify.subprocess, "run", lambda *args, **kwargs: Completed())
    assert orca_notify.capture_notification_terminal(tmp_path) is None


def test_completion_is_sent_to_captured_terminal(monkeypatch):
    handle = "term_12345678-1234-1234-1234-123456789abc"
    monkeypatch.setattr(orca_notify, "_orca_executable", lambda: "orca")
    captured = {}
    class Completed:
        returncode = 0
        stdout = "{}"
        stderr = ""
    def run(argv, **kwargs):
        captured["argv"] = argv
        return Completed()
    monkeypatch.setattr(orca_notify.subprocess, "run", run)
    task_result = {"task_id": "TASK-000123", "status": "COMPLETED", "verification": {"status": "PASS", "changed_paths": ["done.txt"], "checks": [{"type": "command", "exit_code": 0}]}, "test_result": {"status": "PASS"}}
    result = orca_notify.send_completion_to_orca({"notification_terminal": handle}, task_result)
    assert result == {"status": "DELIVERED", "terminal": handle}
    assert captured["argv"][:5] == ["orca", "terminal", "send", "--terminal", handle]
    assert "TASK-000123" in captured["argv"][6]
    assert '"verified": true' in captured["argv"][6]
    assert '"changed_files": ["done.txt"]' in captured["argv"][6]
    assert "--enter" in captured["argv"]
