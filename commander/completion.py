"""Completion report sinks and durable client notification events."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .task_store import canonical_project_root, read_json, state_root, write_json


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def resolve_report_path(project_root, relative_path):
    """Resolve a repository-relative report path without allowing escape."""
    if relative_path is None:
        return None
    if not isinstance(relative_path, str) or not relative_path.strip():
        raise ValueError("completion_report must be a non-empty repository-relative path")
    normalized = relative_path.replace("\\", "/")
    if normalized.startswith("/") or ":" in normalized or ".." in normalized.split("/"):
        raise ValueError("completion_report must be a repository-relative path")
    root = Path(project_root).resolve()
    target = (root / normalized).resolve()
    if not target.is_relative_to(root):
        raise ValueError("completion_report must remain inside project_root")
    return target


def select_completion_report(project_root, requested=None):
    """Use an explicit sink, or an existing benchmark result at project root."""
    if requested is not None:
        resolve_report_path(project_root, requested)
        return requested.replace("\\", "/")
    default = Path(project_root).resolve() / "BENCHMARK_RESULT.json"
    return "BENCHMARK_RESULT.json" if default.is_file() else None


def update_completion_report(spec, result, started_at, finished_at):
    """Atomically merge verified task facts into the configured project report."""
    relative = spec.get("completion_report")
    if not relative:
        return None
    target = resolve_report_path(spec["project_root"], relative)
    existing = read_json(target) if target.exists() else {}
    if not isinstance(existing, dict):
        raise ValueError("completion report root must be a JSON object")
    usage = result.get("usage", {})
    metrics = result.get("cost_metrics", {})
    worker = metrics.get("worker", {})
    verification = result.get("verification", {})
    checks = [item for item in verification.get("checks", []) if item.get("type") == "command"]
    actual_root = str(Path(spec["project_root"]).resolve())
    expected_root = existing.get("expected_project_root")
    existing.update(
        run_id=existing.get("run_id") or result["task_id"],
        actual_project_root=actual_root,
        comparison_valid=(canonical_project_root(expected_root) == canonical_project_root(actual_root)) if expected_root else True,
        started_at=started_at,
        finished_at=finished_at,
        elapsed_seconds=worker.get("elapsed_seconds"),
        tests={
            "status": result.get("test_result", {}).get("status", "UNAVAILABLE"),
            "commands_passed": sum(item.get("exit_code") == 0 for item in checks),
            "commands_failed": sum(item.get("exit_code") != 0 for item in checks),
        },
        agentcommander_task_id=result["task_id"],
        worker_metrics={
            "runtime_seconds": worker.get("elapsed_seconds"),
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "cache_read_tokens": usage.get("cache_read_tokens"),
            "cache_write_tokens": usage.get("cache_write_tokens"),
            "local_repairs": metrics.get("repair_count", 0),
            "loop_guard_warnings": metrics.get("loop_guard_warnings", 0),
            "loop_guard_stops": metrics.get("loop_guard_stops", 0),
            "stop_reason": result.get("stop_reason"),
        },
        completion={
            "status": result.get("status"),
            "verified": verification.get("status") == "PASS",
            "summary": result.get("worker_summary", ""),
            "changed_files": verification.get("changed_paths", result.get("worker_files_changed", [])),
            "written_at": _now(),
        },
    )
    write_json(target, existing)
    return str(target)


def event_path(task_id, base=None):
    return Path(base or state_root()) / "state" / "completion_events" / f"{task_id}.json"


def record_completion_event(spec, result, finished_at, report_path=None, report_error=None, base=None):
    event = {
        "event": "agentcommander.task.completed",
        "task_id": result["task_id"],
        "project_root": canonical_project_root(spec["project_root"]),
        "status": result.get("status"),
        "verified": result.get("verification", {}).get("status") == "PASS",
        "summary": result.get("worker_summary", ""),
        "finished_at": finished_at,
        "completion_report": report_path,
        "report_error": report_error,
        "delivered_at": None,
        "acknowledged_at": None,
    }
    write_json(event_path(result["task_id"], base), event)
    return event


def list_completion_events(project_root=None, include_acknowledged=False, base=None):
    folder = Path(base or state_root()) / "state" / "completion_events"
    events = []
    wanted = canonical_project_root(project_root) if project_root else None
    for path in sorted(folder.glob("TASK-*.json")):
        event = read_json(path)
        if wanted and event.get("project_root") != wanted:
            continue
        if not include_acknowledged and event.get("acknowledged_at"):
            continue
        events.append(event)
    return events


def mark_event(task_id, field, base=None):
    path = event_path(task_id, base)
    event = read_json(path)
    event[field] = _now()
    write_json(path, event)
    return event
