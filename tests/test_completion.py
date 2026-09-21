import json
import asyncio

import pytest

from commander.completion import (
    list_completion_events,
    mark_event,
    record_completion_event,
    resolve_report_path,
    select_completion_report,
    update_completion_report,
)
from commander import server


def sample_result(task_id="TASK-000123"):
    return {
        "task_id": task_id,
        "status": "COMPLETED",
        "stop_reason": None,
        "worker_summary": "done",
        "worker_files_changed": ["src/a.py"],
        "usage": {"input_tokens": 10, "output_tokens": 20, "cache_read_tokens": 30, "cache_write_tokens": 0},
        "verification": {"status": "PASS", "changed_paths": ["src/a.py"], "checks": [{"type": "command", "exit_code": 0}]},
        "test_result": {"status": "PASS"},
        "cost_metrics": {"repair_count": 0, "loop_guard_warnings": 0, "loop_guard_stops": 0, "worker": {"elapsed_seconds": 12.5}},
    }


def test_existing_benchmark_report_is_selected_and_filled(tmp_path):
    report = tmp_path / "BENCHMARK_RESULT.json"
    report.write_text(json.dumps({"expected_project_root": str(tmp_path), "codex_usage": "keep"}), encoding="utf-8")
    assert select_completion_report(tmp_path) == "BENCHMARK_RESULT.json"
    target = update_completion_report({"project_root": str(tmp_path), "completion_report": "BENCHMARK_RESULT.json"}, sample_result(), "start", "finish")
    data = json.loads(report.read_text(encoding="utf-8"))
    assert target == str(report)
    assert data["agentcommander_task_id"] == "TASK-000123"
    assert data["worker_metrics"]["input_tokens"] == 10
    assert data["tests"]["commands_passed"] == 1
    assert data["codex_usage"] == "keep"
    assert data["comparison_valid"] is True


def test_report_path_cannot_escape_project(tmp_path):
    with pytest.raises(ValueError, match="repository-relative"):
        resolve_report_path(tmp_path, "../outside.json")
    with pytest.raises(ValueError, match="repository-relative"):
        resolve_report_path(tmp_path, "C:/outside.json")


def test_completion_events_are_durable_filterable_and_acknowledgeable(tmp_path):
    project = tmp_path / "repo"
    project.mkdir()
    result = sample_result()
    record_completion_event({"project_root": str(project)}, result, "finish", "report.json", None, tmp_path)
    events = list_completion_events(project_root=project, base=tmp_path)
    assert [item["task_id"] for item in events] == ["TASK-000123"]
    assert events[0]["completion_report"] == "report.json"
    mark_event("TASK-000123", "acknowledged_at", tmp_path)
    assert list_completion_events(base=tmp_path) == []
    assert len(list_completion_events(include_acknowledged=True, base=tmp_path)) == 1


def test_connected_client_receives_completion_notice(monkeypatch):
    event = {"task_id": "TASK-000123", "event": "agentcommander.task.completed"}
    delivered = []

    class Session:
        async def send_log_message(self, level, data, logger=None):
            delivered.append((level, data, logger))

    class Context:
        session = Session()

    monkeypatch.setattr(server, "status", lambda task_id: {"status": "COMPLETED"})
    monkeypatch.setattr(server, "list_completion_events", lambda **kwargs: [event])
    monkeypatch.setattr(server, "mark_event", lambda task_id, field: event)
    asyncio.run(server._push_completion_notice("TASK-000123", Context()))
    assert delivered == [("notice", event, "agent-commander")]


def test_disconnected_client_keeps_durable_notice_unacknowledged(monkeypatch):
    event = {"task_id": "TASK-000123", "event": "agentcommander.task.completed", "delivered_at": None}
    marked = []

    class Session:
        async def send_log_message(self, *args, **kwargs):
            raise ConnectionError("client disconnected")

    class Context:
        session = Session()

    monkeypatch.setattr(server, "status", lambda task_id: {"status": "COMPLETED"})
    monkeypatch.setattr(server, "list_completion_events", lambda **kwargs: [event])
    monkeypatch.setattr(server, "mark_event", lambda task_id, field: marked.append((task_id, field)))
    asyncio.run(server._push_completion_notice("TASK-000123", Context()))
    assert marked == []
    assert event["delivered_at"] is None
