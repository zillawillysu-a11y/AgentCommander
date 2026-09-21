import time
import subprocess
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .config import load_config
from .config import update_mode
from .handoff import export_handoff, load_handoff
from .models import discover_pi_models, list_profiles, resolve_profile
from .pi_worker import launch
from .task_store import ACTIVE, FINAL, assign_worker_slot, claim_worker_slot, create_task, create_repair_task, list_tasks as store_list, read_json, release_worker_slot, repo_id, status, task_dir, write_json
from .subprocess_utils import hidden_run_kwargs
from .verifier import changed_paths, verify

mcp = FastMCP("agent-commander", log_level="ERROR")
WORKER_TIMEOUTS = {"SMALL": 1800, "NORMAL": 3600, "LARGE": 5400}
MAX_WORKER_TIMEOUT = 14400
TASK_KINDS = {"IMPLEMENT", "REFACTOR", "TEST", "DIAGNOSE", "SEARCH", "REVIEW"}


def select_worker_timeout(task_scope="NORMAL", requested=None, repair_of=None):
    """Choose a bounded worker lifetime; unrelated to wait_for_task polling."""
    scope = str(task_scope).upper()
    if scope not in WORKER_TIMEOUTS:
        raise ValueError("task_scope must be SMALL, NORMAL, or LARGE")
    timeout = requested if requested is not None else WORKER_TIMEOUTS["SMALL" if repair_of else scope]
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout < 1 or timeout > MAX_WORKER_TIMEOUT:
        raise ValueError(f"timeout_seconds range is 1–{MAX_WORKER_TIMEOUT}")
    return timeout


def _validate(project_root, objective, acceptance_criteria, allowed_paths, verification_commands, timeout_seconds, task_kind):
    root = Path(project_root).resolve()
    if not root.is_dir() or not (root / ".git").exists():
        raise ValueError("project_root 必須是現有 Git repository")
    if not objective.strip() or not acceptance_criteria or not allowed_paths:
        raise ValueError("缺少 objective、acceptance_criteria 或 allowed_paths")
    for pattern in allowed_paths:
        normalized = pattern.replace("\\", "/")
        if normalized.startswith("/") or ":" in normalized or ".." in normalized.split("/"):
            raise ValueError("allowed_paths 必須是 repository 內的相對路徑")
    if not isinstance(verification_commands, list) or any(not isinstance(cmd, list) or not cmd or not all(isinstance(arg, str) for arg in cmd) for cmd in verification_commands):
        raise ValueError("verification_commands 必須是 argv 陣列清單")
    if timeout_seconds < 1 or timeout_seconds > MAX_WORKER_TIMEOUT:
        raise ValueError(f"timeout_seconds 範圍為 1–{MAX_WORKER_TIMEOUT}")
    if task_kind not in TASK_KINDS:
        raise ValueError(f"task_kind must be one of {', '.join(sorted(TASK_KINDS))}")
    if any(s["status"] in ACTIVE for s in store_list()):
        raise ValueError("V0.1 同時只能執行一個 Pi Worker")
    return root


@mcp.tool()
def delegate_pi(project_root: str, objective: str, acceptance_criteria: list[str], allowed_paths: list[str], verification_commands: list[list[str]], timeout_seconds: int | None = None, optional_context: str = "", required_files: list[str] | None = None, repair_of: str | None = None, model_profile: str | None = None, task_scope: str = "NORMAL", max_output_tokens: int | None = None, task_kind: str = "IMPLEMENT") -> dict:
    """啟動全新 Pi/Qwen Milestone。task_scope: SMALL=1800, NORMAL=3600, LARGE=5400 秒；repair 預設 SMALL。"""
    config = load_config()
    if config["mode"] == "OFF":
        raise ValueError("AGENT_COMMANDER_DISABLED")
    timeout_seconds = select_worker_timeout(task_scope, timeout_seconds, repair_of)
    selected_profile, pi_model = resolve_profile(model_profile, config)
    task_kind = str(task_kind).upper()
    root = _validate(project_root, objective, acceptance_criteria, allowed_paths, verification_commands, timeout_seconds, task_kind)
    for relative in required_files or []:
        candidate = (root / relative).resolve()
        if not candidate.is_relative_to(root):
            raise ValueError("required_files 必須在 repository 內")
    if repair_of:
        original = read_json(task_dir(repair_of) / "task.json")
        if Path(original["project_root"]).resolve() != root:
            raise ValueError("repair project_root 必須與原任務一致")
    output_limit = max_output_tokens if max_output_tokens is not None else config["worker"].get("max_output_tokens")
    if output_limit is not None and (not isinstance(output_limit, int) or isinstance(output_limit, bool) or output_limit < 1):
        raise ValueError("max_output_tokens must be a positive integer or null")
    spec = {"project_root": str(root), "objective": objective, "acceptance_criteria": acceptance_criteria, "allowed_paths": allowed_paths, "verification_commands": verification_commands, "timeout_seconds": timeout_seconds, "task_scope": task_scope.upper(), "task_kind": task_kind, "max_output_tokens": output_limit, "optional_context": optional_context[:8000], "required_files": required_files or [], "repair_of": repair_of, "model_profile": selected_profile, "pi_model": pi_model, "preexisting_paths": changed_paths(root), "shared_worktree": True}
    handoff = load_handoff(root)
    if handoff:
        spec["portable_handoff"] = "\n\n".join(f"{name}:\n{content[:6000]}" for name, content in handoff.items())[:12000]
    baseline = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, stdin=subprocess.DEVNULL, capture_output=True, text=True, **hidden_run_kwargs())
    if baseline.returncode == 0:
        spec["baseline_head"] = baseline.stdout.strip()
    else:
        empty_tree = subprocess.run(["git", "hash-object", "-t", "tree", "--stdin"], cwd=root, input=b"", capture_output=True, **hidden_run_kwargs())
        if empty_tree.returncode:
            raise ValueError("無法建立 Git 基線")
        spec["baseline_head"] = empty_tree.stdout.decode("ascii").strip()
        spec["baseline_is_tree"] = True
    claim_worker_slot()
    task_id = None
    try:
        task_id = create_repair_task(repair_of, spec, load_config()["pi"]["max_repairs"]) if repair_of else create_task(spec)
        assign_worker_slot(task_id)
        launched = launch(task_id)
        launched["repo_id"] = repo_id(root)
        launched["commander_action"] = "END_TURN_WHILE_WORKER_RUNS"
        launched["polling_policy"] = "DO_NOT_LOOP_WAIT_FOR_TASK; resume when the user returns"
        return launched
    except OSError as exc:
        if task_id is None:
            release_worker_slot("")
            raise
        folder = task_dir(task_id)
        data = read_json(folder / "status.json")
        data.update(status="FAILED", finished_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat())
        write_json(folder / "status.json", data)
        release_worker_slot(task_id)
        return {"task_id": task_id, "status": "FAILED", "error": str(exc)}
    except Exception:
        if task_id is None:
            release_worker_slot("")
        raise


@mcp.tool()
def get_agentcommander_status() -> dict:
    """Return compact global mode, worker-profile, Pi, and runtime status."""
    config = load_config(); models = discover_pi_models(config)
    tasks = store_list(); latest = tasks[-1] if tasks else None
    latest_task = {key: latest.get(key) for key in ("task_id", "status", "started_at", "finished_at") if latest.get(key) is not None} if latest else None
    return {"mode": config["mode"], "default_profile": config["worker"].get("default_profile"), "pi_available": bool(models), "available_profiles": [x["profile"] for x in list_profiles(config, models) if x["available"]], "runtime_status": "READY", "latest_task": latest_task}


@mcp.tool()
def list_worker_models() -> list[dict]:
    """List configured aliases and whether their Pi models are currently available."""
    return list_profiles()


@mcp.tool()
def set_mode(mode: str) -> dict:
    """Persist OFF, AUTO, or FORCE as the machine-wide default."""
    return {"mode": update_mode(mode)["mode"]}


@mcp.tool()
def get_task_status(task_id: str) -> dict:
    """查詢任務狀態，包含 PID、時間與 exit code。"""
    return status(task_id)


@mcp.tool()
def get_task_result(task_id: str) -> dict:
    """取得精簡結果；完整 JSONL 僅保留在磁碟。"""
    folder = task_dir(task_id)
    path = folder / "result.json"
    if not path.exists():
        return {"task_id": task_id, "status": status(task_id)["status"], "result": "尚未產生"}
    data = read_json(path)
    verification = data.get("verification", {})
    commands = [item for item in verification.get("checks", []) if item.get("type") == "command"]
    usage = data.get("usage", {})
    return {"task_id": task_id, "status": data.get("status"), "verified": verification.get("status") == "PASS", "summary": data.get("worker_summary", ""), "changed_files": verification.get("changed_paths", data.get("worker_files_changed", [])), "tests": {"status": data.get("test_result", {}).get("status", "UNAVAILABLE"), "commands_passed": sum(item.get("exit_code") == 0 for item in commands), "commands_failed": sum(item.get("exit_code") != 0 for item in commands)}, "verification": verification.get("status", "UNAVAILABLE"), "repair_attempts": data.get("cost_metrics", {}).get("repair_count", 0), "elapsed_seconds": data.get("cost_metrics", {}).get("worker", {}).get("elapsed_seconds", "unavailable"), "worker_usage": {key: usage.get(key, "unavailable") for key in ("input_tokens", "output_tokens")}, "stop_reason": data.get("stop_reason")}


@mcp.tool()
def get_task_diagnostics(task_id: str) -> dict:
    """Return bounded failure and guard diagnostics only on explicit request."""
    folder = task_dir(task_id)
    path = folder / "result.json"
    if not path.exists():
        return {"task_id": task_id, "status": status(task_id)["status"], "diagnostics": "not available"}
    data = read_json(path)
    verification = data.get("verification", {})
    checks = [{key: item.get(key) for key in ("type", "argv", "path", "passed", "exit_code", "stdout_tail", "stderr_tail", "stdout_log", "stderr_log") if key in item} for item in verification.get("checks", [])]
    return {"task_id": task_id, "status": data.get("status"), "stop_reason": data.get("stop_reason"), "acceptance_reasons": data.get("final_acceptance", {}).get("reasons", []), "worker_process": data.get("worker_process_result"), "verification_errors": verification.get("errors", []), "checks": checks, "path_validation": verification.get("path_validation"), "generated_artifact_paths": verification.get("generated_artifact_paths", []), "verification_artifact_paths": verification.get("verification_artifact_paths", []), "verification_side_effect_paths": verification.get("verification_side_effect_paths", []), "worker_claim": data.get("worker_claim_result"), "artifacts": data.get("artifacts"), "loop_guard": data.get("loop_guard"), "repair_history": data.get("repair_history", [])}


@mcp.tool()
def verify_task(task_id: str) -> dict:
    """重新執行 deterministic verification。"""
    folder = task_dir(task_id)
    spec = read_json(folder / "task.json")
    result = verify(spec, folder / "verification_logs")
    write_json(folder / "verification.json", result)
    return result


@mcp.tool()
def list_tasks(project_root: str | None = None) -> list[dict]:
    """列出任務與狀態。"""
    return store_list(project_root=project_root)


@mcp.tool()
def get_project_handoff(project_root: str) -> dict:
    """讀取目標 repository 的可攜式精簡交接資料；資料夾不存在可正常運作。"""
    data = load_handoff(project_root)
    return {"project_root": str(Path(project_root).resolve()), "present": data is not None, "files": {name: content[:6000] for name, content in (data or {}).items()}, "truncated": any(len(content) > 6000 for content in (data or {}).values())}


@mcp.tool()
def export_project_handoff(project_root: str, decisions: str | None = None) -> dict:
    """明確匯出三個可 Git tracking 的精簡交接檔；不匯出 logs。"""
    return export_handoff(project_root, decisions)


@mcp.tool(description="Wait once when completion is expected. Never call this in a polling loop; end the Codex turn instead.")
def wait_for_task(task_id: str, timeout_seconds: int = 600) -> dict:
    """在本機等待任務結束，避免高頻 polling。"""
    deadline = time.monotonic() + min(max(timeout_seconds, 1), 3600)
    while time.monotonic() < deadline:
        data = status(task_id)
        if data["status"] in FINAL:
            return get_task_result(task_id)
        time.sleep(2)
    return status(task_id)
