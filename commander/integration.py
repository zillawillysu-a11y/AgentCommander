"""Reversible Codex global integration."""
import os
import shutil
import subprocess
from pathlib import Path

BEGIN = "<!-- AGENTCOMMANDER:BEGIN -->"
END = "<!-- AGENTCOMMANDER:END -->"
RULES = """<!-- AGENTCOMMANDER:BEGIN -->
AgentCommander is available through the `agent-commander` MCP. Before substantial coding work, consult `get_agentcommander_status` when delegation is relevant. OFF means never delegate to Pi/Qwen. AUTO means delegate substantial implementation, debugging, refactoring, repository investigation, tests, or repetitive work when useful. FORCE means strongly prefer delegation for coding implementation. The current user's explicit instruction overrides AUTO/FORCE for that task.

Act as Commander, planner, reviewer, and final authority; Pi/Qwen is an implementation worker. Delegate meaningful milestones using fresh sessions, wait when appropriate, review deterministic verification, repair in a fresh session, and only declare completion after verification. Commit accepted milestones when appropriate. Speak to the user in Traditional Chinese; write worker prompts in English.
<!-- AGENTCOMMANDER:END -->"""

def codex_home(): return Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))
def agents_path(): return codex_home() / "AGENTS.md"

def install_managed_block(path=None):
    path = Path(path or agents_path()); old = path.read_text(encoding="utf-8") if path.exists() else ""
    start, end = old.find(BEGIN), old.find(END)
    if start >= 0 and end >= start: new = old[:start] + RULES + old[end + len(END):]
    else: new = old + (("\n" if old and not old.endswith("\n") else "") + ("\n" if old else "")) + RULES + "\n"
    path.parent.mkdir(parents=True, exist_ok=True); path.write_text(new, encoding="utf-8"); return path

def remove_managed_block(path=None):
    path = Path(path or agents_path())
    if not path.exists(): return False
    old = path.read_text(encoding="utf-8"); start, end = old.find(BEGIN), old.find(END)
    if start < 0 or end < start: return False
    new = old[:start] + old[end + len(END):]
    if new.startswith("\n\n"): new = new[1:]
    path.write_text(new, encoding="utf-8"); return True

def mcp_add_command(helper): return ["codex", "mcp", "add", "agent-commander", "--", str(Path(helper).resolve()), "mcp"]
def _codex(command):
    argv = list(command); argv[0] = shutil.which(argv[0]) or argv[0]
    return subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True)
def install_codex(helper):
    _codex(["codex", "mcp", "remove", "agent-commander"])
    result = _codex(mcp_add_command(helper)); result.check_returncode(); install_managed_block(); return True
def remove_codex():
    result = _codex(["codex", "mcp", "remove", "agent-commander"]); remove_managed_block(); return result.returncode == 0
def integration_status():
    try: mcp = _codex(["codex", "mcp", "get", "agent-commander", "--json"]).returncode == 0
    except OSError: mcp = False
    block = agents_path().exists() and BEGIN in agents_path().read_text(encoding="utf-8")
    return mcp and block
