import time
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .config import load_config
from .pi_worker import launch
from .task_store import TASKS, FINAL, create_task, create_repair_task, list_tasks as store_list, read_json, status, task_dir, write_json
from .verifier import verify

mcp = FastMCP("agent-commander")


def _validate(project_root, objective, acceptance_criteria, allowed_paths, verification_commands, timeout_seconds):
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
    if timeout_seconds < 1 or timeout_seconds > 14400:
        raise ValueError("timeout_seconds 範圍為 1–14400")
    if any(s["status"] in ("QUEUED", "RUNNING") for s in store_list()):
        raise ValueError("V0.1 同時只能執行一個 Pi Worker")
    return root


@mcp.tool()
def delegate_pi(project_root: str, objective: str, acceptance_criteria: list[str], allowed_paths: list[str], verification_commands: list[list[str]], timeout_seconds: int = 2700, optional_context: str = "", required_files: list[str] | None = None, repair_of: str | None = None) -> dict:
    """啟動全新 Pi/Qwen Milestone，立即回傳 durable task ID。"""
    root = _validate(project_root, objective, acceptance_criteria, allowed_paths, verification_commands, timeout_seconds)
    for relative in required_files or []:
        candidate = (root / relative).resolve()
        if not candidate.is_relative_to(root):
            raise ValueError("required_files 必須在 repository 內")
    if repair_of:
        original = read_json(task_dir(repair_of) / "task.json")
        if Path(original["project_root"]).resolve() != root:
            raise ValueError("repair project_root 必須與原任務一致")
    spec = {"project_root": str(root), "objective": objective, "acceptance_criteria": acceptance_criteria, "allowed_paths": allowed_paths, "verification_commands": verification_commands, "timeout_seconds": timeout_seconds, "optional_context": optional_context[:8000], "required_files": required_files or []}
    task_id = create_repair_task(repair_of, spec, load_config()["pi"]["max_repairs"]) if repair_of else create_task(spec)
    try:
        return launch(task_id)
    except OSError as exc:
        folder = task_dir(task_id)
        data = read_json(folder / "status.json")
        data.update(status="FAILED", finished_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat())
        write_json(folder / "status.json", data)
        return {"task_id": task_id, "status": "FAILED", "error": str(exc)}


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
    keys = ("task_id", "milestone", "worker_status", "status", "exit_code", "worker_claim_status", "worker_summary", "worker_files_changed", "usage", "malformed_jsonl_lines", "verification", "warnings", "artifacts")
    return {key: data[key] for key in keys if key in data}


@mcp.tool()
def verify_task(task_id: str) -> dict:
    """重新執行 deterministic verification。"""
    folder = task_dir(task_id)
    spec = read_json(folder / "task.json")
    result = verify(spec, folder / "verification_logs")
    write_json(folder / "verification.json", result)
    return result


@mcp.tool()
def list_tasks() -> list[dict]:
    """列出任務與狀態。"""
    return store_list()


@mcp.tool()
def wait_for_task(task_id: str, timeout_seconds: int = 600) -> dict:
    """在本機等待任務結束，避免高頻 polling。"""
    deadline = time.monotonic() + min(max(timeout_seconds, 1), 3600)
    while time.monotonic() < deadline:
        data = status(task_id)
        if data["status"] in FINAL:
            return get_task_result(task_id)
        time.sleep(2)
    return status(task_id)
