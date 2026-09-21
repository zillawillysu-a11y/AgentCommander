"""Reversible Codex global integration."""
import os
import shutil
import subprocess
from pathlib import Path

BEGIN = "<!-- AGENTCOMMANDER:BEGIN -->"
END = "<!-- AGENTCOMMANDER:END -->"
RULES = """<!-- AGENTCOMMANDER:BEGIN -->
AgentCommander is available through the `agent-commander` MCP. Before substantial coding work, consult `get_agentcommander_status` when delegation is relevant. OFF means never delegate to Pi/Qwen. AUTO means delegate substantial implementation, debugging, refactoring, repository investigation, tests, or repetitive work when useful. FORCE means strongly prefer delegation for coding implementation. The current user's explicit instruction overrides AUTO/FORCE for that task.

Act as Commander, planner, reviewer, and final authority; Pi/Qwen is the implementation worker. Optimize for Codex overhead at or below 30% of a comparable direct task without reducing quality. Do not delegate trivial or obvious one-file work when overhead dominates. Never say a Worker started until `delegate_pi` returns a task ID and RUNNING/QUEUED status.

While a Worker is RUNNING, do not implement the same milestone or repeatedly poll. For work expected to exceed a few minutes, report the task ID, end the Codex turn, and let the user return later; do not loop `wait_for_task`. On return, inspect the compact task result first. When process, claim, tests, path validation, and deterministic verification pass, accept the work without re-investigating the repository. Read files or repair only explicit unmet criteria; never reimplement a verified milestone.

Use SMALL/repair 1800s, normal substantial work 3600s, and clearly large milestones/refactors 5400s. Local Qwen tokens do not consume Codex quota: omit `max_output_tokens` for normal/large work unless the user requests a hard budget or runaway protection. Treat it as an emergency circuit breaker, not a normal target. Qwen token/runtime metrics never prove Codex savings; report Codex usage only when available and compare against similar direct work. Use fresh Worker sessions and Traditional Chinese for users; Worker prompts are English.
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
