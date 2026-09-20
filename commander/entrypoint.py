import json
import os
import sys
from pathlib import Path
from .doctor import doctor_json

def packaged(): return bool(getattr(sys, "frozen", False))
def helper_path(): return Path(sys.executable).with_name("AgentCommanderMCP.exe") if packaged() else None
def worker_command(task_id):
    return [str(helper_path()), "worker", task_id] if packaged() else [sys.executable, "-m", "commander.pi_worker", task_id]

def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv); command = argv[0] if argv else "mcp"
    if command == "mcp":
        from .server import mcp
        try:
            mcp.run(transport="stdio")
        finally:
            # Some stdio transports close Python's wrapper at EOF. PyInstaller's
            # bootloader flushes it once more during shutdown unless it is replaced.
            if getattr(sys.stdout, "closed", False):
                sys.stdout = open(os.devnull, "w")
            if getattr(sys.stderr, "closed", False):
                sys.stderr = open(os.devnull, "w")
    elif command == "worker" and len(argv) == 2:
        from .pi_worker import run
        from .task_store import state_root
        run(argv[1], Path(os.environ.get("AGENT_COMMANDER_STATE_ROOT", state_root())))
    elif command == "doctor" and argv[1:] == ["--json"]: print(doctor_json())
    else: raise SystemExit("Usage: AgentCommanderMCP.exe {mcp|worker TASK-ID|doctor --json}")
