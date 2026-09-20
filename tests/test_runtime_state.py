import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from commander import server, task_store
from commander import pi_worker
from commander.handoff import export_handoff, load_handoff


def git_repo(path):
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "-c", "user.name=test", "-c", "user.email=test@example.com", "commit", "--allow-empty", "-qm", "init"], check=True)
    return path


def task_spec(root, objective="Build one feature"):
    return {"project_root": str(root), "objective": objective, "acceptance_criteria": ["passes"], "allowed_paths": ["src/**"], "verification_commands": [[sys.executable, "-c", "print('ok')"]], "timeout_seconds": 30}


def test_default_localappdata_and_override(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local App Data"))
    assert task_store.state_root() == tmp_path / "Local App Data" / "AgentCommander"
    custom = tmp_path / "custom state"
    assert task_store.state_root({"runtime": {"state_root": str(custom)}}) == custom.resolve()
    with pytest.raises(ValueError, match="AgentCommander"):
        task_store.state_root({"runtime": {"state_root": str(task_store.ROOT / "state" / "new")}})


def test_namespaces_global_ids_metadata_and_git_clean(tmp_path):
    state = tmp_path / "runtime"
    first = git_repo(tmp_path / "專案 A")
    second = git_repo(tmp_path / "project B")
    a = task_store.create_task(task_spec(first), state)
    b = task_store.create_task(task_spec(second), state)
    assert (a, b) == ("TASK-000001", "TASK-000002")
    assert task_store.repo_id(first) != task_store.repo_id(second)
    assert task_store.repo_id(first) == task_store.repo_id(str(first.resolve()))
    if os.name == "nt":
        assert task_store.repo_id(first) == task_store.repo_id(str(first).swapcase())
    assert task_store.task_dir(a, state).parent != task_store.task_dir(b, state).parent
    meta = task_store.read_json(task_store.repo_dir(first, state) / "repo.json")
    assert meta["project_root"] == task_store.canonical_project_root(first)
    assert meta["repo_id"] == task_store.repo_id(first)
    assert meta["created_at"] and meta["last_used_at"]
    assert [item["task_id"] for item in task_store.list_tasks(state, first)] == [a]
    assert [item["task_id"] for item in task_store.list_tasks(state, second)] == [b]
    assert not subprocess.check_output(["git", "-C", str(first), "status", "--porcelain"]).strip()
    assert not subprocess.check_output(["git", "-C", str(second), "status", "--porcelain"]).strip()
    assert task_store.task_dir(a, state).is_relative_to(state)


def test_index_recovery_after_interrupted_creation(tmp_path):
    state = tmp_path / "runtime"
    root = git_repo(tmp_path / "target")
    task_id = task_store.create_task(task_spec(root), state)
    folder = task_store.task_dir(task_id, state)
    index = state / "state" / "task_index" / f"{task_id}.json"
    index.unlink()
    assert task_store.task_dir(task_id, state) == folder
    assert index.exists()
    index.unlink()
    assert task_store.list_tasks(state)[0]["task_id"] == task_id


def test_reject_runtime_inside_target_repo(tmp_path):
    root = git_repo(tmp_path / "target")
    with pytest.raises(ValueError, match="repository 之外"):
        task_store.create_task(task_spec(root), root / "private-state")
    assert not (root / "private-state").exists()


def test_legacy_read_only_and_counter_continuation(tmp_path, monkeypatch):
    state = tmp_path / "runtime"
    legacy = tmp_path / "old" / "tasks"
    folder = legacy / "TASK-000007"
    folder.mkdir(parents=True)
    root = git_repo(tmp_path / "target")
    task_store.write_json(folder / "task.json", task_spec(root))
    task_store.write_json(folder / "status.json", {"task_id": "TASK-000007", "status": "COMPLETED", "pid": None})
    monkeypatch.setattr(task_store, "LEGACY_TASKS", legacy)
    monkeypatch.setattr(task_store, "state_root", lambda config=None: state)
    assert task_store.task_dir("TASK-000007") == folder
    assert task_store.list_tasks()[0]["task_id"] == "TASK-000007"
    new_id = task_store.create_task(task_spec(root))
    assert new_id == "TASK-000008"
    assert folder.exists()
    assert task_store.read_json(folder / "status.json")["status"] == "COMPLETED"
    stale = legacy / "TASK-000009"
    stale.mkdir()
    task_store.write_json(stale / "task.json", task_spec(root))
    task_store.write_json(stale / "status.json", {"task_id": "TASK-000009", "status": "RUNNING", "pid": 99999999})
    assert task_store.status("TASK-000009")["status"] == "INTERRUPTED"
    assert task_store.read_json(stale / "status.json")["status"] == "RUNNING"


def test_optional_handoff_and_clone_without_old_state(tmp_path, monkeypatch):
    state = tmp_path / "runtime"
    root = git_repo(tmp_path / "original")
    assert load_handoff(root) is None
    task_id = task_store.create_task(task_spec(root), state)
    folder = task_store.task_dir(task_id, state)
    result = {"verification": {"status": "PASS", "changed_paths": ["src/core.py"]}, "worker_summary": "done", "usage": {"input_tokens": 123}}
    task_store.write_json(folder / "result.json", result)
    status = task_store.read_json(folder / "status.json")
    status["status"] = "COMPLETED"
    task_store.write_json(folder / "status.json", status)
    exported = export_handoff(root, "# 重要決策\n\n使用標準函式庫。\n", state)
    assert exported["milestone_count"] == 1
    assert export_handoff(root, base=state)["milestone_count"] == 1
    handoff = load_handoff(root)
    assert set(handoff) == {"STATUS.md", "MILESTONES.json", "DECISIONS.md"}
    assert "worker_summary" not in json.dumps(handoff)
    assert "input_tokens" not in json.dumps(handoff)
    assert "worker.jsonl" not in json.dumps(handoff)
    clone = git_repo(tmp_path / "cloned elsewhere")
    shutil.copytree(root / ".agentcommander", clone / ".agentcommander")
    assert task_store.repo_id(root) != task_store.repo_id(clone)
    assert load_handoff(clone)["DECISIONS.md"].endswith("使用標準函式庫。\n")
    new_state = tmp_path / "new machine runtime"
    monkeypatch.setattr(task_store, "state_root", lambda config=None: new_state)
    monkeypatch.setattr(server, "launch", lambda task_id: {"task_id": task_id, "status": "QUEUED"})
    delegated = server.delegate_pi(str(clone), "Continue feature", ["works"], ["src/**"], [[sys.executable, "-c", "print('ok')"]])
    spec = task_store.read_json(task_store.task_dir(delegated["task_id"], new_state) / "task.json")
    assert "使用標準函式庫" in spec["portable_handoff"]
    assert "Build one feature" in spec["portable_handoff"]
    assert not (clone / "state").exists()
    assert export_handoff(clone, base=new_state)["milestone_count"] == 2


def test_handoff_rejects_oversized_file(tmp_path):
    root = git_repo(tmp_path / "project")
    folder = root / ".agentcommander"
    folder.mkdir()
    (folder / "STATUS.md").write_text("x" * 32001, encoding="utf-8")
    with pytest.raises(ValueError, match="太大"):
        load_handoff(root)


def test_background_runner_receives_external_state_root(tmp_path, monkeypatch):
    state = tmp_path / "runtime"
    root = git_repo(tmp_path / "target")
    task_id = task_store.create_task(task_spec(root), state)
    captured = {}

    class FakeProcess:
        pid = 4242

    def fake_popen(argv, **kwargs):
        captured.update(argv=argv, kwargs=kwargs)
        return FakeProcess()

    monkeypatch.setattr(pi_worker.subprocess, "Popen", fake_popen)
    launched = pi_worker.launch(task_id, state)
    assert launched["pid"] == 4242
    assert captured["kwargs"]["env"]["AGENT_COMMANDER_STATE_ROOT"] == str(state)
    assert task_store.task_dir(task_id, state).is_relative_to(state)
