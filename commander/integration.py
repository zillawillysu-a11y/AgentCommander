"""Reversible Codex global integration."""
import os
import shutil
import subprocess
from pathlib import Path
from .config import ROOT

BEGIN = "<!-- AGENTCOMMANDER:BEGIN -->"
END = "<!-- AGENTCOMMANDER:END -->"
RULES = """<!-- AGENTCOMMANDER:BEGIN -->
AgentCommander is available through the `agent-commander` MCP for substantial coding work. Use the `agent-commander` Skill when delegation is appropriate. Do not delegate trivial work when orchestration overhead exceeds the work. The user's explicit instruction always overrides AUTO/FORCE.
<!-- AGENTCOMMANDER:END -->"""

def codex_home(): return Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))
def agents_path(): return codex_home() / "AGENTS.md"
def skill_path(): return Path.home() / ".agents" / "skills" / "agent-commander"

def install_skill(path=None):
    target = Path(path or skill_path()); source = ROOT / "skills" / "agent-commander" / "SKILL.md"
    target.mkdir(parents=True, exist_ok=True); shutil.copyfile(source, target / "SKILL.md"); return target

def remove_skill(path=None):
    target = Path(path or skill_path()); managed = target / "SKILL.md"
    if not managed.exists() or "name: agent-commander" not in managed.read_text(encoding="utf-8"): return False
    managed.unlink()
    try: target.rmdir()
    except OSError: pass
    return True

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
    result = _codex(mcp_add_command(helper)); result.check_returncode(); install_managed_block(); install_skill(); return True
def remove_codex():
    result = _codex(["codex", "mcp", "remove", "agent-commander"]); remove_managed_block(); remove_skill(); return result.returncode == 0
def integration_status():
    try: mcp = _codex(["codex", "mcp", "get", "agent-commander", "--json"]).returncode == 0
    except OSError: mcp = False
    block = agents_path().exists() and BEGIN in agents_path().read_text(encoding="utf-8")
    return mcp and block and (skill_path() / "SKILL.md").exists()
