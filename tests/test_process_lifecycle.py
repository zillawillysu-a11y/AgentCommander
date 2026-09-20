import json
import os
import subprocess
import sys
from pathlib import Path

import psutil
import pytest

from commander import pi_worker, task_store
from commander.process_control import identity_alive


def git_repo(path):
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "-c", "user.name=test", "-c", "user.email=test@example.com", "commit", "--allow-empty", "-qm", "init"], check=True)
    return path


def make_fake_pi(folder):
    script = folder / "fake_pi.py"
    script.write_text(
        "import os,subprocess,sys,time\n"
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(120)'])\n"
        "open(os.path.join(os.getcwd(),'child.pid'),'w').write(str(child.pid))\n"
        "open(os.path.join(os.getcwd(),'parent.pid'),'w').write(str(os.getpid()))\n"
        "time.sleep(120)\n",
        encoding="utf-8",
    )
    wrapper = folder / "fake_pi.cmd"
    wrapper.write_text(f'@echo off\r\n"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
    return wrapper


def task_spec(root, timeout=.5):
    return {"project_root": str(root), "objective": "timeout tree", "acceptance_criteria": ["terminated"], "allowed_paths": ["*.pid"], "verification_commands": [[sys.executable, "-c", "pass"]], "timeout_seconds": timeout, "required_files": []}


@pytest.mark.skipif(os.name != "nt", reason="Windows process-tree integration")
def test_timeout_kills_entire_owned_tree_and_releases_slot(tmp_path, monkeypatch):
    base = tmp_path / "runtime"
    root = git_repo(tmp_path / "project")
    wrapper = make_fake_pi(tmp_path)
    task_store.claim_worker_slot(base)
    task_id = task_store.create_task(task_spec(root), base)
    task_store.assign_worker_slot(task_id, base)
    monkeypatch.setattr(pi_worker, "load_config", lambda: {"pi": {"command": str(wrapper)}})
    original_terminate = pi_worker.terminate_task
    observed = []
    def recording_terminate(*args, **kwargs):
        result = original_terminate(*args, **kwargs)
        observed.append(task_store.read_json(task_store.task_dir(task_id, base) / "status.json")["status"])
        return result
    monkeypatch.setattr(pi_worker, "terminate_task", recording_terminate)
    pi_worker.run(task_id, base)

    result = task_store.read_json(task_store.task_dir(task_id, base) / "result.json")
    termination = task_store.read_json(task_store.task_dir(task_id, base) / "termination.json")
    assert observed == ["TERMINATING"]
    assert result["status"] == "TIMED_OUT"
    assert termination["confirmed_gone"] is True
    assert len(termination["owned_processes"]) >= 2
    assert not any(identity_alive(item) for item in termination["owned_processes"])
    child_pid = int((root / "child.pid").read_text())
    parent_pid = int((root / "parent.pid").read_text())
    assert not psutil.pid_exists(child_pid)
    assert not psutil.pid_exists(parent_pid)

    task_store.claim_worker_slot(base)
    second = task_store.create_task(task_spec(root, 30), base)
    task_store.assign_worker_slot(second, base)
    assert second != task_id
    task_store.release_worker_slot(second, base)


def test_atomic_slot_prevents_two_workers(tmp_path):
    base = tmp_path / "runtime"
    task_store.claim_worker_slot(base)
    with pytest.raises(ValueError, match="WORKER_SLOT_BUSY"):
        task_store.claim_worker_slot(base)


@pytest.mark.parametrize("state", ["QUEUED", "RUNNING", "TERMINATING", "TERMINATION_FAILED", "ORPHAN_WORKER"])
def test_every_uncertain_lifecycle_state_blocks_overlap(tmp_path, state):
    base = tmp_path / state
    root = git_repo(tmp_path / f"repo-{state}")
    task_id = task_store.create_task(task_spec(root, 30), base)
    folder = task_store.task_dir(task_id, base)
    data = task_store.read_json(folder / "status.json")
    data["status"] = state
    data["pid"] = os.getpid() if state == "RUNNING" else None
    task_store.write_json(folder / "status.json", data)
    if state in {"TERMINATING", "TERMINATION_FAILED", "ORPHAN_WORKER"}:
        process = psutil.Process()
        task_store.write_json(folder / "worker_process.json", {"task_id": task_id, "pi_root": {"pid": process.pid, "create_time": process.create_time()}, "descendants": []})
    with pytest.raises(ValueError, match="WORKER_SLOT_BUSY"):
        task_store.claim_worker_slot(base)


def test_final_state_with_live_owned_process_becomes_orphan(tmp_path):
    base = tmp_path / "runtime"
    root = git_repo(tmp_path / "project")
    task_id = task_store.create_task(task_spec(root, 30), base)
    folder = task_store.task_dir(task_id, base)
    data = task_store.read_json(folder / "status.json")
    data.update(status="TIMED_OUT", pid=None, finished_at="premature")
    task_store.write_json(folder / "status.json", data)
    process = psutil.Process()
    task_store.write_json(folder / "worker_process.json", {"task_id": task_id, "pi_root": {"pid": process.pid, "create_time": process.create_time()}, "descendants": []})
    assert task_store.status(task_id, base)["status"] == "ORPHAN_WORKER"
    with pytest.raises(ValueError, match="ORPHAN_WORKER"):
        task_store.claim_worker_slot(base)


def test_unconfirmed_termination_keeps_slot_busy(tmp_path, monkeypatch):
    base = tmp_path / "runtime"
    root = git_repo(tmp_path / "project")
    task_store.claim_worker_slot(base)
    task_id = task_store.create_task(task_spec(root, .01), base)
    task_store.assign_worker_slot(task_id, base)

    class FakeProcess:
        pid = 765432
        returncode = None
        def poll(self): return None

    class FakeJob:
        assigned = False
        def __init__(self, process): pass
        def close(self): pass

    monkeypatch.setattr(pi_worker.subprocess, "Popen", lambda *args, **kwargs: FakeProcess())
    monkeypatch.setattr(pi_worker, "WindowsJob", FakeJob)
    current = psutil.Process()
    monkeypatch.setattr(pi_worker, "identity", lambda pid: {"pid": pid, "create_time": current.create_time() if pid == current.pid else 1.0})
    monkeypatch.setattr(pi_worker, "capture_tree", lambda root: [])
    monkeypatch.setattr(pi_worker, "terminate_task", lambda *args, **kwargs: {"confirmed_gone": False})
    monkeypatch.setattr(pi_worker, "load_config", lambda: {"pi": {"command": "fake-pi"}})
    pi_worker.run(task_id, base)

    assert task_store.read_json(task_store.task_dir(task_id, base) / "status.json")["status"] == "TERMINATION_FAILED"
    assert task_store.worker_slot_path(base).exists()
    with pytest.raises(ValueError, match="WORKER_SLOT_BUSY"):
        task_store.claim_worker_slot(base)


@pytest.mark.skipif(os.name != "nt" or not os.environ.get("AGENTCOMMANDER_PACKAGED_HELPER"), reason="set packaged helper path")
def test_packaged_helper_kills_child_tree(tmp_path):
    helper = Path(os.environ["AGENTCOMMANDER_PACKAGED_HELPER"])
    local = tmp_path / "Local App Data"
    base = local / "AgentCommander"
    root = git_repo(tmp_path / "packaged project")
    wrapper = make_fake_pi(tmp_path)
    base.mkdir(parents=True)
    (base / "config.json").write_text(json.dumps({"pi": {"command": str(wrapper)}}), encoding="utf-8")
    task_store.claim_worker_slot(base)
    task_id = task_store.create_task(task_spec(root), base)
    task_store.assign_worker_slot(task_id, base)
    env = os.environ.copy()
    env.update(LOCALAPPDATA=str(local), AGENT_COMMANDER_STATE_ROOT=str(base))
    completed = subprocess.run([str(helper), "worker", task_id], env=env, stdin=subprocess.DEVNULL, capture_output=True, timeout=45)
    assert completed.returncode == 0
    result = task_store.read_json(task_store.task_dir(task_id, base) / "result.json")
    termination = task_store.read_json(task_store.task_dir(task_id, base) / "termination.json")
    assert result["status"] == "TIMED_OUT"
    assert termination["confirmed_gone"] is True
    assert not any(identity_alive(item) for item in termination["owned_processes"])
    task_store.claim_worker_slot(base)
    next_task = task_store.create_task(task_spec(root, 30), base)
    task_store.assign_worker_slot(next_task, base)
    assert next_task != task_id
    task_store.release_worker_slot(next_task, base)
