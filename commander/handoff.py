"""Optional compact project handoff stored in the target Git repository."""

import json
import os
from pathlib import Path

from .task_store import list_tasks, read_json, repo_id, task_dir, write_json

FILES = ("STATUS.md", "MILESTONES.json", "DECISIONS.md")
MAX_FILE_BYTES = 32_000


def handoff_dir(project_root):
    root = Path(project_root).resolve()
    folder = (root / ".agentcommander").resolve()
    if not folder.is_relative_to(root):
        raise ValueError(".agentcommander 必須在目標 repository 內")
    return folder


def load_handoff(project_root):
    folder = handoff_dir(project_root)
    if not folder.exists():
        return None
    content = {}
    for name in FILES:
        file = folder / name
        if not file.exists():
            continue
        if not file.resolve().is_relative_to(Path(project_root).resolve()):
            raise ValueError(f"交接檔案超出目標 repository：{name}")
        if not file.is_file() or file.stat().st_size > MAX_FILE_BYTES:
            raise ValueError(f"交接檔案無效或太大：{name}")
        text = file.read_text(encoding="utf-8")
        if name == "MILESTONES.json":
            data = json.loads(text)
            if not isinstance(data, dict) or not isinstance(data.get("milestones"), list) or not all(isinstance(item, dict) for item in data["milestones"]):
                raise ValueError("MILESTONES.json 格式無效")
        content[name] = text
    if not content:
        return None
    return content


def _write_text(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".{os.getpid()}.tmp")
    temp.write_text(content, encoding="utf-8")
    os.replace(temp, path)


def export_handoff(project_root, decisions=None, base=None):
    """Write only compact, reviewable files. Never write logs or raw metrics."""
    root = Path(project_root).resolve()
    if not root.is_dir() or not (root / ".git").exists():
        raise ValueError("project_root 必須是 Git repository")
    folder = handoff_dir(root)
    previous = load_handoff(root)
    records = json.loads(previous["MILESTONES.json"]).get("milestones", []) if previous and "MILESTONES.json" in previous else []
    seen = {record.get("record_id") for record in records if isinstance(record, dict)}
    for entry in list_tasks(base, root)[-50:]:
        task_id = entry["task_id"]
        task = read_json(task_dir(task_id, base) / "task.json")
        result_path = task_dir(task_id, base) / "result.json"
        result = read_json(result_path) if result_path.exists() else {}
        verification = result.get("verification", {})
        record_id = f"{repo_id(root)}:{task_id}"
        record = {"record_id": record_id, "milestone": task.get("objective", "")[:500], "status": entry["status"], "verification": verification.get("status", "UNKNOWN"), "changed_files": verification.get("changed_paths", [])[:50]}
        if record_id in seen:
            records = [existing for existing in records if existing.get("record_id") != record_id]
        records.append(record)
        seen.add(record_id)
    records = records[-50:]
    summary = "# AgentCommander 專案狀態\n\n"
    if records:
        summary += "\n".join(f"- {record['milestone']}：{record['status']}，驗證 {record['verification']}" for record in records[-10:]) + "\n"
    else:
        summary += "尚無本機任務紀錄；可從此交接資料繼續規劃。\n"
    if len(summary.encode("utf-8")) > MAX_FILE_BYTES:
        raise ValueError("STATUS.md 太大")
    milestones = {"schema_version": 1, "milestones": records}
    encoded = json.dumps(milestones, ensure_ascii=False, indent=2)
    if len(encoded.encode("utf-8")) > MAX_FILE_BYTES:
        raise ValueError("MILESTONES.json 太大")
    existing_decisions = folder / "DECISIONS.md"
    if existing_decisions.exists() and not existing_decisions.resolve().is_relative_to(root):
        raise ValueError("DECISIONS.md 超出目標 repository")
    decision_text = decisions if decisions is not None else existing_decisions.read_text(encoding="utf-8") if existing_decisions.exists() else "# 重要決策\n\n"
    if len(decision_text.encode("utf-8")) > MAX_FILE_BYTES:
        raise ValueError("DECISIONS.md 太大")
    _write_text(folder / "STATUS.md", summary)
    write_json(folder / "MILESTONES.json", milestones)
    _write_text(existing_decisions, decision_text)
    return {"project_root": str(root), "files": [str(folder / name) for name in FILES], "milestone_count": len(records)}
