"""Owned Pi process-tree tracking and termination."""
import os
import subprocess
import time
from datetime import datetime, timezone

import psutil
from .subprocess_utils import hidden_run_kwargs


def now():
    return datetime.now(timezone.utc).isoformat()


def identity(pid):
    try:
        process = psutil.Process(int(pid))
        return {"pid": process.pid, "create_time": process.create_time()}
    except (psutil.Error, OSError, ValueError, TypeError):
        return None


def identity_alive(item):
    if not item or not item.get("pid"):
        return False
    try:
        process = psutil.Process(int(item["pid"]))
        expected = item.get("create_time")
        return expected is None or abs(process.create_time() - float(expected)) < 0.01
    except (psutil.Error, OSError, ValueError, TypeError):
        return False


def capture_tree(root):
    if not identity_alive(root):
        return []
    try:
        process = psutil.Process(int(root["pid"]))
        return [item for child in process.children(recursive=True) if (item := identity(child.pid))]
    except (psutil.Error, OSError):
        return []


def merge_identities(*groups):
    merged = {}
    for group in groups:
        for item in group or []:
            if item and item.get("pid"):
                merged[(int(item["pid"]), item.get("create_time"))] = item
    return list(merged.values())


def owned_alive(record, refresh=True):
    root = record.get("pi_root") or ({"pid": record.get("pid"), "create_time": record.get("create_time")} if record.get("pid") else None)
    descendants = record.get("descendants", [])
    if refresh and identity_alive(root):
        descendants = merge_identities(descendants, capture_tree(root))
    candidates = ([root] if root else []) + descendants
    return [item for item in candidates if identity_alive(item)]


class WindowsJob:
    """Best-effort kill-on-close Job Object; taskkill remains the recovery path."""
    def __init__(self, process):
        self.handle = None
        if os.name != "nt":
            return
        try:
            import ctypes
            from ctypes import wintypes

            class IO_COUNTERS(ctypes.Structure):
                _fields_ = [(name, ctypes.c_ulonglong) for name in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount", "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

            class BASIC_LIMIT(ctypes.Structure):
                _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong), ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t), ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD), ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

            class EXTENDED_LIMIT(ctypes.Structure):
                _fields_ = [("BasicLimitInformation", BASIC_LIMIT), ("IoInfo", IO_COUNTERS), ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CreateJobObjectW.restype = wintypes.HANDLE
            kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
            kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            handle = kernel32.CreateJobObjectW(None, None)
            if not handle:
                return
            info = EXTENDED_LIMIT()
            info.BasicLimitInformation.LimitFlags = 0x00002000  # KILL_ON_JOB_CLOSE
            if not kernel32.SetInformationJobObject(handle, 9, ctypes.byref(info), ctypes.sizeof(info)) or not kernel32.AssignProcessToJobObject(handle, int(process._handle)):
                kernel32.CloseHandle(handle)
                return
            self.handle = handle
            self._close = kernel32.CloseHandle
        except Exception:
            self.handle = None

    @property
    def assigned(self):
        return self.handle is not None

    def close(self):
        if self.handle is not None:
            self._close(self.handle)
            self.handle = None


def terminate_task(task_id, base=None, confirmation_seconds=15):
    """Terminate only the recorded Pi tree and confirm every recorded identity is gone."""
    from .task_store import read_json, task_dir, write_json

    folder = task_dir(task_id, base)
    status_path, process_path = folder / "status.json", folder / "worker_process.json"
    data = read_json(status_path)
    if not process_path.exists():
        result = {"task_id": task_id, "attempted_at": now(), "confirmed_gone": True, "owned_processes": [], "method": "no-process-record"}
        write_json(folder / "termination.json", result)
        return result
    record = read_json(process_path)
    root = record.get("pi_root") or ({"pid": record.get("pid"), "create_time": record.get("create_time")} if record.get("pid") else None)
    record["pi_root"] = root
    record["descendants"] = merge_identities(record.get("descendants"), capture_tree(root))
    record["termination_started_at"] = now()
    write_json(process_path, record)
    data.update(status="TERMINATING", termination_started_at=record["termination_started_at"])
    write_json(status_path, data)

    methods = []
    if root and identity_alive(root) and os.name == "nt":
        methods.append("taskkill")
        subprocess.run(["taskkill", "/PID", str(root["pid"]), "/T", "/F"], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30, **hidden_run_kwargs())
    alive = owned_alive(record)
    if alive:
        methods.append("psutil")
        for item in reversed(alive):
            try:
                psutil.Process(int(item["pid"])).kill()
            except psutil.Error:
                pass
    deadline = time.monotonic() + confirmation_seconds
    while time.monotonic() < deadline:
        alive = owned_alive(record, refresh=False)
        if not alive:
            break
        time.sleep(0.1)
    confirmed = not owned_alive(record, refresh=False)
    result = {"task_id": task_id, "attempted_at": record["termination_started_at"], "finished_at": now(), "confirmed_gone": confirmed, "owned_processes": ([root] if root else []) + record["descendants"], "remaining": owned_alive(record, refresh=False), "method": "+".join(methods) or "already-gone"}
    write_json(folder / "termination.json", result)
    record.update(termination_finished_at=result["finished_at"], termination_confirmed=confirmed)
    write_json(process_path, record)
    if not confirmed:
        data.update(status="TERMINATION_FAILED", finished_at=None, termination_error="OWNED_PROCESS_TREE_STILL_ALIVE")
        write_json(status_path, data)
    return result


def reconcile_status(folder, data):
    """Reconcile durable state against recorded process identities without killing anything."""
    from .task_store import LEGACY_TASKS, write_json

    process_path = folder / "worker_process.json"
    if folder.is_relative_to(LEGACY_TASKS) or not process_path.exists():
        return data
    record = __import__("json").loads(process_path.read_text(encoding="utf-8"))
    alive = owned_alive(record)
    state = data.get("status")
    helper = record.get("helper") or ({"pid": data.get("pid"), "create_time": None} if data.get("pid") else None)
    helper_alive = identity_alive(helper)
    if state == "RUNNING" and not helper_alive:
        data.update(status="ORPHAN_WORKER" if alive else "INTERRUPTED", finished_at=None if alive else now())
        write_json(folder / "status.json", data)
    elif state in {"COMPLETED", "FAILED", "TIMED_OUT", "INTERRUPTED"} and alive:
        data.update(status="ORPHAN_WORKER", finished_at=None, termination_error="FINAL_STATE_WITH_LIVE_OWNED_PROCESS")
        write_json(folder / "status.json", data)
    elif state in {"TERMINATION_FAILED", "ORPHAN_WORKER", "TERMINATING"} and not alive and not helper_alive:
        data.update(status="INTERRUPTED", finished_at=now())
        write_json(folder / "status.json", data)
    return data
