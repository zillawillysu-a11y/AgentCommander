import json
import subprocess
import sys
from pathlib import Path

import pytest

from commander.config import DEFAULT, load_config
from commander.pi_parser import parse_jsonl
from commander.task_store import create_task, create_repair_task, read_json, status, task_dir, write_json
from commander.verifier import allowed, bounded, changed_paths, verify
import commander.verifier as verifier_module


def repo(path):
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "-c", "user.name=test", "-c", "user.email=test@example.com", "commit", "--allow-empty", "-qm", "init"], check=True)
    return path


def spec(root):
    return {"project_root": str(root), "objective": "測試", "acceptance_criteria": ["檔案存在"], "allowed_paths": ["src/**", "tests/**"], "verification_commands": [[sys.executable, "-c", "print('ok')"]], "timeout_seconds": 10, "required_files": []}


def test_config_fallback_and_override(tmp_path):
    assert load_config(tmp_path) == DEFAULT
    (tmp_path / "config.local.json").write_text('{"pi":{"max_repairs":1}}', encoding="utf-8")
    assert load_config(tmp_path)["pi"]["max_repairs"] == 1
    (tmp_path / "config.local.json").write_text('{"worker":{"fresh_session":false}}', encoding="utf-8")
    with pytest.raises(ValueError):
        load_config(tmp_path)


def test_ids_persistence_and_interrupted(tmp_path):
    base = tmp_path / "任務 state"
    a = create_task({"name": "一", "project_root": str(tmp_path / "目標")}, base)
    b = create_task({"name": "二", "project_root": str(tmp_path / "目標")}, base)
    assert (a, b) == ("TASK-000001", "TASK-000002")
    assert read_json(task_dir(a, base) / "task.json")["name"] == "一"
    data = read_json(task_dir(a, base) / "status.json")
    data.update(status="RUNNING", pid=99999999)
    write_json(task_dir(a, base) / "status.json", data)
    assert status(a, base)["status"] == "INTERRUPTED"
    assert status(a, base)["status"] == "INTERRUPTED"


def test_repair_limit(tmp_path):
    base = tmp_path / "tasks"
    target = str(tmp_path / "target")
    original = create_task({"objective": "first", "project_root": target}, base)
    data = read_json(task_dir(original, base) / "status.json")
    data["status"] = "FAILED"
    write_json(task_dir(original, base) / "status.json", data)
    assert create_repair_task(original, {"objective": "repair", "project_root": target}, 2, base).endswith("REPAIR-1")
    assert create_repair_task(original, {"objective": "repair", "project_root": target}, 2, base).endswith("REPAIR-2")
    with pytest.raises(ValueError, match="limit"):
        create_repair_task(original, {"project_root": target}, 2, base)


def test_parser_malformed_and_claim(tmp_path):
    file = tmp_path / "events.jsonl"
    file.write_text('bad\n' + json.dumps({"type": "message_end", "message": {"role": "assistant", "model": "local", "usage": {"input": 2, "output": 3}, "content": [{"type": "text", "text": '{"status":"PASS","summary":"ok"}'}]}}) + '\n', encoding="utf-8")
    parsed = parse_jsonl(file)
    assert parsed["malformed_lines"] == 1
    assert parsed["claim"]["status"] == "PASS"
    assert parsed["usage"]["input_tokens"] == 2
    assert bounded("\n".join(map(str, range(200))), 2) == "198\n199"


def test_parser_detects_truncated_worker_prompt(tmp_path):
    file = tmp_path / "events.jsonl"
    user = {"type": "message_end", "message": {"role": "user", "content": [{"type": "text", "text": "Only the first line"}]}}
    file.write_text(json.dumps(user) + "\n", encoding="utf-8")
    assert parse_jsonl(file, "TASK-000123", "Build store")["prompt_delivered"] is False
    user["message"]["content"][0]["text"] = "TASK-000123 Build store"
    file.write_text(json.dumps(user) + "\n", encoding="utf-8")
    assert parse_jsonl(file, "TASK-000123", "Build store")["prompt_delivered"] is True


def test_git_and_allowed_paths(tmp_path):
    root = repo(tmp_path / "含 空格 中文")
    (root / "src").mkdir()
    (root / "src" / "a.py").write_text("x = 1\n")
    assert changed_paths(root) == ["src/a.py"]
    assert allowed("src/a.py", ["src/**"])
    result = verify(spec(root), tmp_path / "verification_logs")
    assert result["status"] == "PASS"
    assert result["changed_paths"] == ["src/a.py"]
    assert (tmp_path / "verification_logs" / "command_1.stdout.log").exists()
    (root / "README.md").write_text("oops\n")
    result = verify(spec(root))
    assert "OUTSIDE_ALLOWED_PATHS" in result["errors"]


def test_plain_directory_allowed_path_includes_descendants():
    assert allowed("src/todo.py", ["src"])
    assert allowed("tests/unit/test_todo.py", ["tests/"])
    assert not allowed("src2/todo.py", ["src"])


def test_changed_paths_git_status_timeout_is_bounded(tmp_path, monkeypatch):
    root = repo(tmp_path / "git-timeout")
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs.get("timeout"))
    monkeypatch.setattr(verifier_module.subprocess, "run", timeout)
    with pytest.raises(RuntimeError, match="git status timed out after 30 seconds"):
        changed_paths(root)


def test_preexisting_commander_change_is_not_worker_outside_path(tmp_path):
    root = repo(tmp_path / "commander-dirty")
    (root / "src").mkdir()
    (root / "src" / "worker.py").write_text("worker\n")
    (root / "README.md").write_text("Commander edit\n")
    result = verify({**spec(root), "allowed_paths": ["src/**"], "preexisting_paths": ["README.md"]})
    assert result["status"] == "PASS"
    assert result["preexisting_outside_paths"] == ["README.md"]
    assert result["path_validation"]["commander_or_existing_paths"] == ["README.md"]


def test_worker_change_to_forbidden_path_is_rejected(tmp_path):
    root = repo(tmp_path / "worker-outside")
    (root / "src").mkdir()
    (root / "src" / "worker.py").write_text("worker\n")
    (root / "README.md").write_text("Worker edit\n")
    result = verify({**spec(root), "allowed_paths": ["src/**"], "preexisting_paths": [], "worker_claimed_paths": ["README.md"]})
    assert result["status"] == "FAIL"
    assert "OUTSIDE_ALLOWED_PATHS" in result["errors"]
    assert result["path_validation"]["outside_allowed_paths"] == ["README.md"]


def test_concurrent_forbidden_path_is_reported_unknown_not_worker_violation(tmp_path):
    root = repo(tmp_path / "concurrent-unknown")
    (root / "src").mkdir()
    (root / "src" / "worker.py").write_text("worker\n")
    (root / "README.md").write_text("Commander concurrent edit\n")
    result = verify({**spec(root), "allowed_paths": ["src/**"], "preexisting_paths": [], "shared_worktree": True})
    assert result["status"] == "FAIL"
    assert "ATTRIBUTION_UNKNOWN" in result["errors"]
    assert "OUTSIDE_ALLOWED_PATHS" not in result["errors"]
    assert result["path_validation"]["attribution_unknown_paths"] == ["README.md"]


def test_committed_worker_changes_still_checked(tmp_path):
    root = repo(tmp_path / "committed")
    baseline = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    (root / "README.md").write_text("outside\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.name=test", "-c", "user.email=test@example.com", "commit", "-qm", "worker commit"], check=True)
    data = spec(root)
    data["baseline_head"] = baseline
    result = verify(data)
    assert result["changed_paths"] == ["README.md"]
    assert result["insertions"] == 1
    assert "OUTSIDE_ALLOWED_PATHS" in result["errors"]


def test_unborn_repository_baseline(tmp_path):
    root = tmp_path / "new"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    tree = subprocess.run(["git", "hash-object", "-t", "tree", "--stdin"], cwd=root, input=b"", capture_output=True, check=True).stdout.decode().strip()
    data = spec(root)
    data.update(baseline_head=tree, baseline_is_tree=True)
    (root / "README.md").write_text("outside\n", encoding="utf-8")
    assert "OUTSIDE_ALLOWED_PATHS" in verify(data)["errors"]
    subprocess.run(["git", "-C", str(root), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.name=test", "-c", "user.email=test@example.com", "commit", "-qm", "worker commit"], check=True)
    assert "OUTSIDE_ALLOWED_PATHS" in verify(data)["errors"]


def test_verification_fail_and_diff_check(tmp_path):
    root = repo(tmp_path / "target")
    file = root / "src" / "file.py"
    file.parent.mkdir()
    file.write_text("x = 1\n")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.name=test", "-c", "user.email=test@example.com", "commit", "-qm", "base"], check=True)
    file.write_text("x = 2   \n")
    data = spec(root)
    data["verification_commands"] = [[sys.executable, "-c", "raise SystemExit(1)"]]
    result = verify(data)
    assert "GIT_DIFF_CHECK_FAILED" in result["errors"]
    assert "VERIFICATION_COMMAND_FAILED" in result["errors"]


def test_mcp_tools_exposed():
    from commander.server import mcp
    assert mcp.name == "agent-commander"


def test_worker_launch_failure_persists_result(tmp_path, monkeypatch):
    from commander import pi_worker
    root = repo(tmp_path / "project")
    base = tmp_path / "task state"
    task_id = create_task(spec(root), base)
    monkeypatch.setattr(pi_worker, "load_config", lambda: {"pi": {"command": "nonexistent_pi_command_9f42"}})
    pi_worker.run(task_id, base)
    result = read_json(task_dir(task_id, base) / "result.json")
    assert result["status"] == "FAILED"
    assert result["worker_status"] == "LAUNCH_FAILED"
    assert (task_dir(task_id, base) / "worker.jsonl").exists()


def test_worker_timeout(tmp_path, monkeypatch):
    from commander import pi_worker
    root = repo(tmp_path / "project")
    base = tmp_path / "task state"
    data = spec(root)
    data["timeout_seconds"] = .01
    task_id = create_task(data, base)
    executable = tmp_path / "slow_pi.cmd"
    executable.write_text("@echo off\r\nping -n 5 127.0.0.1 >nul\r\n", encoding="ascii")
    monkeypatch.setattr(pi_worker, "load_config", lambda: {"pi": {"command": str(executable)}})
    pi_worker.run(task_id, base)
    result = read_json(task_dir(task_id, base) / "result.json")
    assert result["status"] == "TIMED_OUT"
    prompt_file = task_dir(task_id, base) / "worker_prompt.md"
    assert prompt_file.exists()
    assert "TASK SPECIFICATION" in prompt_file.read_text(encoding="utf-8")
    assert read_json(task_dir(task_id, base) / "metadata.json")["fresh_session"] is True
