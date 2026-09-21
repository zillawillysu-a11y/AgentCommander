"""Best-effort Orca terminal wake-up integration for completed tasks."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from .subprocess_utils import hidden_run_kwargs

TERMINAL_HANDLE = re.compile(r"term_[0-9a-f-]{36}")


def _orca_executable():
    configured = os.environ.get("ORCA_CODEX_LAUNCH_PREFLIGHT")
    if configured and Path(configured).is_file():
        return configured
    discovered = shutil.which("orca")
    if discovered:
        return discovered
    local = os.environ.get("LOCALAPPDATA")
    if local:
        installed = Path(local) / "Programs" / "orca" / "resources" / "bin" / "orca.exe"
        if installed.is_file():
            return str(installed)
    return None


def capture_notification_terminal(project_root):
    """Capture the originating Orca terminal, with a unique-project fallback."""
    inherited = os.environ.get("ORCA_TERMINAL_HANDLE", "")
    if TERMINAL_HANDLE.fullmatch(inherited):
        return inherited
    executable = _orca_executable()
    if not executable:
        return None
    try:
        completed = subprocess.run(
            [executable, "terminal", "list", "--worktree", f"path:{Path(project_root).resolve()}", "--json"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            **hidden_run_kwargs(),
        )
        payload = json.loads(completed.stdout) if completed.returncode == 0 else {}
        terminals = payload.get("result", {}).get("terminals", [])
        candidates = [
            item
            for item in terminals
            if item.get("agentIdentity") == "codex"
            and item.get("connected") is True
            and item.get("writable") is True
            and not item.get("orphaned")
            and TERMINAL_HANDLE.fullmatch(item.get("handle", ""))
        ]
        if len(candidates) == 1:
            return candidates[0]["handle"]
        if len(candidates) > 1:
            ranked = sorted(candidates, key=lambda item: item.get("lastOutputAt") or 0, reverse=True)
            newest = ranked[0].get("lastOutputAt") or 0
            second = ranked[1].get("lastOutputAt") or 0
            now_ms = int(time.time() * 1000)
            if newest > second and now_ms - newest <= 120_000:
                return ranked[0]["handle"]
        return None
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError, KeyError, TypeError):
        return None


def send_completion_to_orca(spec, result):
    """Inject a fixed completion prompt into the originating Codex terminal."""
    handle = spec.get("notification_terminal")
    if not handle:
        return {"status": "UNAVAILABLE", "reason": "NO_ORCA_TERMINAL"}
    if not TERMINAL_HANDLE.fullmatch(str(handle)):
        return {"status": "FAILED", "reason": "INVALID_ORCA_TERMINAL"}
    executable = _orca_executable()
    if not executable:
        return {"status": "UNAVAILABLE", "reason": "ORCA_CLI_NOT_FOUND"}
    task_id = result["task_id"]
    task_status = result.get("status", "UNKNOWN")
    verification = result.get("verification", {})
    checks = [item for item in verification.get("checks", []) if item.get("type") == "command"]
    changed_files = verification.get("changed_paths", result.get("worker_files_changed", []))[:20]
    outcome = {
        "task_id": task_id,
        "status": task_status,
        "verified": verification.get("status") == "PASS",
        "tests_status": result.get("test_result", {}).get("status", "UNAVAILABLE"),
        "commands_passed": sum(item.get("exit_code") == 0 for item in checks),
        "commands_failed": sum(item.get("exit_code") != 0 for item in checks),
        "changed_files": changed_files,
        "stop_reason": result.get("stop_reason"),
    }
    text = (
        "AgentCommander completion signal. Continue this conversation now and report the following verified runtime outcome to the user: "
        f"{json.dumps(outcome, ensure_ascii=False)}. "
        "Do not wait or poll. If the AgentCommander MCP transport is available, you may fetch the compact result and acknowledge the notification; "
        "if it is unavailable, report this embedded outcome directly."
    )
    try:
        completed = subprocess.run(
            [executable, "terminal", "send", "--terminal", handle, "--text", text, "--enter", "--json"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            **hidden_run_kwargs(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"status": "FAILED", "reason": f"{type(exc).__name__}: {exc}"[:500]}
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "orca terminal send failed").strip()[-500:]
        return {"status": "FAILED", "reason": detail}
    return {"status": "DELIVERED", "terminal": handle}
