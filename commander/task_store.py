import json
import os
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TASKS = ROOT / "state" / "tasks"
FINAL = {"COMPLETED", "FAILED", "TIMED_OUT", "INTERRUPTED"}


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".{os.getpid()}.tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def task_dir(task_id, base=TASKS):
    if not __import__("re").fullmatch(r"TASK-\d{6}(?:-REPAIR-[12])?", task_id):
        raise ValueError("Invalid task ID")
    return Path(base) / task_id


def create_task(spec, base=TASKS):
    base = Path(base)
    base.mkdir(parents=True, exist_ok=True)
    counter = base / "counter.txt"
    # Exclusive lock serializes IDs across MCP server processes.
    lock = base / ".counter.lock"
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
        number = int(counter.read_text() if counter.exists() else "0") + 1
        counter.write_text(str(number), encoding="ascii")
        task_id = f"TASK-{number:06d}"
        folder = task_dir(task_id, base)
        folder.mkdir()
        write_json(folder / "task.json", spec)
        write_json(folder / "status.json", {"task_id": task_id, "status": "QUEUED", "pid": None, "started_at": None, "finished_at": None, "exit_code": None})
        return task_id
    finally:
        lock.unlink(missing_ok=True)


def create_repair_task(original_id, spec, max_repairs=2, base=TASKS):
    if not __import__("re").fullmatch(r"TASK-\d{6}", original_id):
        raise ValueError("repair_of must be an original task ID")
    original = task_dir(original_id, base)
    if not (original / "task.json").exists():
        raise FileNotFoundError(original_id)
    if status(original_id, base)["status"] not in FINAL:
        raise ValueError("Original task is still running")
    for attempt in range(1, max_repairs + 1):
        task_id = f"{original_id}-REPAIR-{attempt}"
        folder = task_dir(task_id, base)
        try:
            folder.mkdir()
        except FileExistsError:
            continue
        write_json(folder / "task.json", {**spec, "repair_of": original_id, "repair_attempt": attempt})
        write_json(folder / "status.json", {"task_id": task_id, "status": "QUEUED", "pid": None, "started_at": None, "finished_at": None, "exit_code": None})
        return task_id
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
            result = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True, text=True)
            return f'"{pid}"' in result.stdout
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


def status(task_id, base=TASKS):
    folder = task_dir(task_id, base)
    data = read_json(folder / "status.json")
    if data["status"] == "RUNNING" and not pid_alive(data.get("pid")):
        data.update(status="INTERRUPTED", finished_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat())
        write_json(folder / "status.json", data)
    return data


def list_tasks(base=TASKS):
    return [status(p.name, base) for p in sorted(Path(base).glob("TASK-*")) if (p / "status.json").exists()]
