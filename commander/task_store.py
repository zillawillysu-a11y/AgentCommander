"""Durable, per-repository runtime state outside Git worktrees."""

import hashlib
import json
import os
import re
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from .config import ROOT, load_config
from .subprocess_utils import hidden_run_kwargs

LEGACY_TASKS = ROOT / "state" / "tasks"
FINAL = {"COMPLETED", "PARTIAL", "FAILED", "TIMED_OUT", "INTERRUPTED", "OUTPUT_LIMIT_REACHED", "LOOP_GUARD_STOPPED", "COMPLEXITY_BUDGET_REACHED"}
ACTIVE = {"QUEUED", "RUNNING", "TERMINATING", "TERMINATION_FAILED", "ORPHAN_WORKER"}
TASK_ID = re.compile(r"TASK-\d{6,}(?:-REPAIR-\d+)?")


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def state_root(config=None):
    configured = (config or load_config())["runtime"]["state_root"]
    if configured:
        path = Path(os.path.expandvars(os.path.expanduser(configured))).resolve()
        if path.is_relative_to(ROOT):
            raise ValueError("Runtime state 不可放在 AgentCommander repository 內")
        return path
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / "AgentCommander"
    if os.name == "nt":
        return Path.home() / "AppData" / "Local" / "AgentCommander"
    return Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "AgentCommander"


def canonical_project_root(project_root):
    path = Path(project_root).resolve()
    normalized = unicodedata.normalize("NFC", str(path))
    return os.path.normcase(normalized) if os.name == "nt" else normalized


def repo_id(project_root):
    return hashlib.sha256(canonical_project_root(project_root).encode("utf-8")).hexdigest()[:16]


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".{os.getpid()}.tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def repo_dir(project_root, base=None):
    base = Path(base) if base is not None else state_root()
    return base / "state" / "repos" / repo_id(project_root)


def _include_legacy(base):
    return Path(base).resolve() == state_root().resolve()


def ensure_repo(project_root, base=None):
    root = canonical_project_root(project_root)
    base = Path(base) if base is not None else state_root()
    resolved_base = base.resolve()
    if resolved_base.is_relative_to(Path(root)) or resolved_base.is_relative_to(ROOT):
        raise ValueError("Runtime state 必須位於 AgentCommander 與目標 Git repository 之外")
    folder = repo_dir(root, base)
    metadata = folder / "repo.json"
    if metadata.exists():
        data = read_json(metadata)
        if data.get("project_root") != root or data.get("repo_id") != repo_id(root):
            raise RuntimeError("Repository ID collision or invalid metadata")
        data["last_used_at"] = utc_now()
    else:
        data = {"project_root": root, "repo_id": repo_id(root), "created_at": utc_now(), "last_used_at": utc_now()}
    write_json(metadata, data)
    return folder


def task_dir(task_id, base=None):
    if not TASK_ID.fullmatch(task_id):
        raise ValueError("Invalid task ID")
    base = Path(base) if base is not None else state_root()
    index = base / "state" / "task_index" / f"{task_id}.json"
    if index.exists():
        entry = read_json(index)
        rid = entry.get("repo_id", "")
        if not re.fullmatch(r"[0-9a-f]{16}", rid):
            raise ValueError("Invalid repository index")
        return base / "state" / "repos" / rid / "tasks" / task_id
    matches = list((base / "state" / "repos").glob(f"*/tasks/{task_id}"))
    if len(matches) == 1:
        rid = matches[0].parents[1].name
        if re.fullmatch(r"[0-9a-f]{16}", rid):
            write_json(index, {"repo_id": rid})
            return matches[0]
    if len(matches) > 1:
        raise RuntimeError("Duplicate task ID in runtime state")
    # Legacy records stay in place and remain readable. Never delete or move them.
    legacy = LEGACY_TASKS / task_id
    if _include_legacy(base) and legacy.exists():
        return legacy
    raise FileNotFoundError(task_id)


def _allocate_task_id(base):
    base = Path(base)
    state = base / "state"
    state.mkdir(parents=True, exist_ok=True)
    counter = state / "counter.txt"
    lock = state / ".counter.lock"
    for _ in range(100):
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            break
        except FileExistsError:
            time.sleep(.05)
    else:
        raise RuntimeError("Task counter locked")
    try:
        if counter.exists():
            number = int(counter.read_text(encoding="ascii"))
        else:
            existing = list((state / "task_index").glob("TASK-*.json"))
            if _include_legacy(base):
                existing += list(LEGACY_TASKS.glob("TASK-*"))
            number = max((int(match.group(1)) for path in existing if (match := re.match(r"TASK-(\d{6,})", path.stem))), default=0)
        number += 1
        counter.write_text(str(number), encoding="ascii")
        return f"TASK-{number:06d}"
    finally:
        lock.unlink(missing_ok=True)


def _save_new_task(task_id, spec, base):
    project_root = spec["project_root"]
    repo = ensure_repo(project_root, base)
    folder = repo / "tasks" / task_id
    folder.mkdir(parents=True, exist_ok=False)
    write_json(folder / "task.json", spec)
    write_json(folder / "status.json", {"task_id": task_id, "repo_id": repo_id(project_root), "project_root": canonical_project_root(project_root), "status": "QUEUED", "pid": None, "started_at": None, "finished_at": None, "exit_code": None})
    write_json(Path(base) / "state" / "task_index" / f"{task_id}.json", {"repo_id": repo_id(project_root)})
    return task_id


def create_task(spec, base=None):
    base = Path(base) if base is not None else state_root()
    if "project_root" not in spec:
        raise ValueError("project_root required")
    ensure_repo(spec["project_root"], base)
    task_id = _allocate_task_id(base)
    return _save_new_task(task_id, spec, base)


def create_repair_task(original_id, spec, max_repairs=2, base=None):
    base = Path(base) if base is not None else state_root()
    if not re.fullmatch(r"TASK-\d{6,}", original_id):
        raise ValueError("repair_of must be an original task ID")
    original = task_dir(original_id, base)
    if status(original_id, base)["status"] not in FINAL:
        raise ValueError("Original task is still running")
    if canonical_project_root(read_json(original / "task.json")["project_root"]) != canonical_project_root(spec["project_root"]):
        raise ValueError("Repair project_root differs from original")
    for attempt in range(1, max_repairs + 1):
        task_id = f"{original_id}-REPAIR-{attempt}"
        if (base / "state" / "task_index" / f"{task_id}.json").exists() or (_include_legacy(base) and (LEGACY_TASKS / task_id).exists()):
            continue
        return _save_new_task(task_id, {**spec, "repair_of": original_id, "repair_attempt": attempt}, base)
    raise ValueError("Repair limit reached")


def pid_alive(pid):
    if not pid:
        return False
    try:
        import psutil
        return psutil.pid_exists(pid)
    except ImportError:
        if os.name == "nt":
            import subprocess
            result = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True, text=True, **hidden_run_kwargs())
            return f'"{pid}"' in result.stdout
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


def worker_slot_path(base=None):
    return Path(base) / "state" / "worker_slot.json" if base is not None else state_root() / "state" / "worker_slot.json"


def claim_worker_slot(base=None):
    """Atomically reserve the one global worker slot before task creation."""
    base = Path(base) if base is not None else state_root()
    blockers = [item for item in list_tasks(base) if item.get("status") in ACTIVE]
    if blockers:
        raise ValueError(f"WORKER_SLOT_BUSY: {blockers[0]['task_id']} {blockers[0]['status']}")
    path = worker_slot_path(base)
    path.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump({"status": "CLAIMING", "owner_pid": os.getpid(), "claimed_at": utc_now()}, stream)
            return path
        except FileExistsError:
            try:
                slot = read_json(path)
                task_id = slot.get("task_id")
                if task_id and status(task_id, base).get("status") in ACTIVE:
                    raise ValueError(f"WORKER_SLOT_BUSY: {task_id}")
                age = time.time() - path.stat().st_mtime
                if not task_id and age < 60:
                    raise ValueError("WORKER_SLOT_BUSY: delegation claim in progress")
                path.unlink(missing_ok=True)
            except FileNotFoundError:
                continue
    raise ValueError("WORKER_SLOT_BUSY")


def assign_worker_slot(task_id, base=None):
    base = Path(base) if base is not None else state_root()
    path = worker_slot_path(base)
    slot = read_json(path)
    slot.update(task_id=task_id, status="OWNED")
    write_json(path, slot)


def release_worker_slot(task_id, base=None):
    base = Path(base) if base is not None else state_root()
    path = worker_slot_path(base)
    if not path.exists():
        return
    slot = read_json(path)
    if slot.get("task_id") in (None, task_id):
        path.unlink(missing_ok=True)


def status(task_id, base=None):
    folder = task_dir(task_id, base)
    data = read_json(folder / "status.json")
    if not folder.is_relative_to(LEGACY_TASKS):
        from .process_control import reconcile_status
        data = reconcile_status(folder, data)
    if data["status"] == "RUNNING" and not pid_alive(data.get("pid")):
        data.update(status="INTERRUPTED", finished_at=utc_now())
        if not folder.is_relative_to(LEGACY_TASKS):
            write_json(folder / "status.json", data)
    return data


def list_tasks(base=None, project_root=None):
    base = Path(base) if base is not None else state_root()
    for folder in (base / "state" / "repos").glob("*/tasks/TASK-*"):
        if (folder / "status.json").exists():
            task_dir(folder.name, base)  # Restores an index interrupted during task creation.
    indexes = sorted((base / "state" / "task_index").glob("TASK-*.json"))
    items = [status(p.stem, base) for p in indexes]
    # Reading legacy state is a compatibility path, not an automatic migration.
    if _include_legacy(base):
        for folder in sorted(LEGACY_TASKS.glob("TASK-*")):
            if (folder / "status.json").exists() and not (base / "state" / "task_index" / f"{folder.name}.json").exists():
                items.append(status(folder.name, base))
    if project_root is not None:
        canonical = canonical_project_root(project_root)
        items = [item for item in items if canonical_project_root(item.get("project_root") or read_json(task_dir(item["task_id"], base) / "task.json")["project_root"]) == canonical]
    return sorted(items, key=lambda item: item["task_id"])
