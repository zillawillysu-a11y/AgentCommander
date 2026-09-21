import json

from commander.config import DEFAULT
from commander.loop_guard import LoopGuard
from commander import server
from commander.pi_worker import is_mechanical_failure, repair_prompt
from commander.task_store import create_task, task_dir, write_json


def start(call_id, name="read", args=None):
    return {"type": "tool_execution_start", "toolCallId": call_id, "toolName": name, "args": args or {"path": "a.py"}}


def end(call_id, name="read", result="same", error=False):
    return {"type": "tool_execution_end", "toolCallId": call_id, "toolName": name, "result": {"content": [{"type": "text", "text": result}]}, "isError": error}


def test_duplicate_call_and_equivalent_failure_guards():
    guard = LoopGuard(DEFAULT["guard"], "SMALL")
    assert guard.observe(start("1")) is None
    assert guard.observe(start("2")) is None
    assert guard.observe(start("3")) == "DUPLICATE_TOOL_CALL"
    guard = LoopGuard(DEFAULT["guard"], "SMALL")
    for n in range(3):
        guard.observe(start(str(n), "bash", {"command": "pytest"}))
        reason = guard.observe(end(str(n), "bash", "exit 1 at 12345", True))
    assert reason == "REPEATED_FAILURE"


def test_cycle_and_tool_budget_guards():
    settings = {**DEFAULT["guard"], "tool_budgets": {"SMALL": 99, "NORMAL": 99, "LARGE": 99}}
    guard = LoopGuard(settings, "SMALL")
    for n, path in enumerate(["a", "b"] * 3):
        reason = guard.observe(start(str(n), args={"path": path}))
    assert reason == "CYCLIC_TOOL_PATTERN"
    settings = {**DEFAULT["guard"], "tool_budgets": {"SMALL": 2, "NORMAL": 2, "LARGE": 2}}
    guard = LoopGuard(settings, "SMALL")
    guard.observe(start("1", args={"path": "a"})); guard.observe(start("2", args={"path": "b"}))
    assert guard.observe(start("3", args={"path": "c"})) == "TOOL_BUDGET_REACHED"


def test_progressing_edit_test_sequence_is_not_stopped():
    guard = LoopGuard(DEFAULT["guard"], "NORMAL")
    events = [("edit", "a"), ("bash", "test1"), ("edit", "b"), ("bash", "test2")]
    for n, (name, value) in enumerate(events):
        assert guard.observe(start(str(n), name, {"value": value}), f"repo-{n}") is None
        assert guard.observe(end(str(n), name, value, name == "bash"), f"repo-{n}") is None


def test_compact_result_omits_logs_and_checks(tmp_path, monkeypatch):
    root = tmp_path / "repo"; root.mkdir()
    base = tmp_path / "state"
    task_id = create_task({"project_root": str(root)}, base)
    folder = task_dir(task_id, base)
    huge = "x" * 20000
    write_json(folder / "result.json", {"task_id": task_id, "status": "COMPLETED", "worker_summary": "done", "usage": {"input_tokens": 12, "output_tokens": 3}, "verification": {"status": "PASS", "changed_paths": ["src/a.py"], "checks": [{"type": "command", "argv": ["pytest"], "exit_code": 0, "stdout_tail": huge, "stderr_tail": huge}]}, "test_result": {"status": "PASS"}, "cost_metrics": {"repair_count": 0, "worker": {"elapsed_seconds": 2}}})
    monkeypatch.setattr(server, "task_dir", lambda _task_id: folder)
    result = server.get_task_result(task_id)
    encoded = json.dumps(result).encode()
    assert len(encoded) <= 2048
    assert b"stdout" not in encoded and b"stderr" not in encoded and b"checks" not in encoded
    diagnostics = server.get_task_diagnostics(task_id)
    assert diagnostics["checks"][0]["stdout_tail"] == huge


def test_repair_classification_and_compact_fresh_prompt():
    assert is_mechanical_failure({"errors": ["VERIFICATION_COMMAND_FAILED"]})
    assert not is_mechanical_failure({"errors": ["VERIFICATION_COMMAND_FAILED", "OUTSIDE_ALLOWED_PATHS"]})
    spec = {"objective": "fix test", "acceptance_criteria": ["passes"], "allowed_paths": ["src/**"]}
    text = repair_prompt("TASK-000001", spec, {"errors": ["VERIFICATION_COMMAND_FAILED"], "checks": [{"type": "command", "argv": ["pytest"], "exit_code": 1, "stderr_tail": "failure"}]}, ["src/a.py"], 1)
    assert "previous reasoning" not in text.lower()
    assert '"failing_command"' in text and '"changed_files"' in text


def test_task_kind_validation(tmp_path):
    root = tmp_path / "repo"; root.mkdir(); (root / ".git").mkdir()
    try:
        server._validate(str(root), "work", ["done"], ["src/**"], [["pytest"]], 10, "ARCHITECT")
    except ValueError as exc:
        assert "task_kind" in str(exc)
    else:
        raise AssertionError("invalid task kind accepted")
