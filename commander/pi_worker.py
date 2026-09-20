import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from .config import ROOT, load_config
from .entrypoint import worker_command
from .models import pi_model_args
from .pi_parser import parse_jsonl
from .process_control import WindowsJob, capture_tree, identity, merge_identities, owned_alive, terminate_task
from .task_store import read_json, release_worker_slot, state_root, task_dir, write_json
from .verifier import verify


def now():
    return datetime.now(timezone.utc).isoformat()


def prompt(task_id, spec):
    template = (ROOT / "prompts" / "pi_worker_en.md").read_text(encoding="utf-8")
    values = {"task_id": task_id, **spec}
    return template.replace("{{TASK}}", json.dumps(values, ensure_ascii=False, indent=2))


def launch(task_id, base=None):
    base = Path(base) if base is not None else state_root()
    folder = task_dir(task_id, base)
    runner = worker_command(task_id)
    env = os.environ.copy()
    env["AGENT_COMMANDER_STATE_ROOT"] = str(base)
    kwargs = {"cwd": str(ROOT), "env": env, "stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(runner, **kwargs)
    data = read_json(folder / "status.json")
    if data["status"] == "QUEUED":
        data.update(status="RUNNING", pid=proc.pid, started_at=now())
        write_json(folder / "status.json", data)
    return {"task_id": task_id, "status": "RUNNING", "pid": proc.pid}


def run(task_id, base=None):
    base = Path(base) if base is not None else state_root()
    folder = task_dir(task_id, base)
    spec = read_json(folder / "task.json")
    config = load_config()
    data = read_json(folder / "status.json")
    data.update(status="RUNNING", pid=os.getpid(), started_at=data.get("started_at") or now())
    write_json(folder / "status.json", data)
    raw = folder / "worker.jsonl"
    stderr = folder / "worker.stderr.log"
    executable = shutil.which(config["pi"]["command"]) or config["pi"]["command"]
    prompt_path = folder / "worker_prompt.md"
    prompt_path.write_text(prompt(task_id, spec), encoding="utf-8")
    model_args = pi_model_args(spec["pi_model"]) if spec.get("pi_model") else []
    command = [executable, *model_args, "--mode", "json", "--print", "--no-session", "--", f"@{prompt_path}", "Complete the attached worker task. Follow its result contract."]
    write_json(folder / "metadata.json", {"invocation_flags": command[1:command.index("--")], "model_profile": spec.get("model_profile"), "pi_model": spec.get("pi_model"), "fresh_session": True, "prompt_file": str(prompt_path), "project_root": spec["project_root"]})
    exit_code = None
    timed_out = False
    termination_failed = False
    failure = None
    job = None
    try:
        with raw.open("wb") as out, stderr.open("wb") as err:
            kwargs = {"cwd": spec["project_root"], "stdin": subprocess.DEVNULL, "stdout": out, "stderr": err}
            if os.name == "nt":
                kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                kwargs["start_new_session"] = True
            proc = subprocess.Popen(command, **kwargs)
            job = WindowsJob(proc)
            record = {"task_id": task_id, "helper_pid": os.getpid(), "helper": identity(os.getpid()), "pi_root": identity(proc.pid) or {"pid": proc.pid, "create_time": None}, "descendants": [], "started_at": now(), "job_object_assigned": job.assigned}
            write_json(folder / "worker_process.json", record)
            deadline = __import__("time").monotonic() + spec["timeout_seconds"]
            last_snapshot = 0.0
            while proc.poll() is None and __import__("time").monotonic() < deadline:
                moment = __import__("time").monotonic()
                if moment - last_snapshot >= 1:
                    record["descendants"] = merge_identities(record["descendants"], capture_tree(record["pi_root"]))
                    record["last_observed_at"] = now()
                    write_json(folder / "worker_process.json", record)
                    last_snapshot = moment
                __import__("time").sleep(0.1)
            if proc.poll() is None:
                timed_out = True
                record["descendants"] = merge_identities(record["descendants"], capture_tree(record["pi_root"]))
                record["termination_method_hint"] = "job-close" if job.assigned else "process-tree"
                write_json(folder / "worker_process.json", record)
                if job.assigned:
                    job.close()
                termination = terminate_task(task_id, base)
                termination_failed = not termination["confirmed_gone"]
                if not termination_failed:
                    try:
                        exit_code = proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        termination_failed = True
            else:
                exit_code = proc.returncode
                record["descendants"] = merge_identities(record["descendants"], capture_tree(record["pi_root"]))
                write_json(folder / "worker_process.json", record)
                if job.assigned:
                    job.close()
                if owned_alive(record):
                    termination = terminate_task(task_id, base)
                    termination_failed = not termination["confirmed_gone"]
    except OSError as exc:
        failure = f"Pi launch failed: {exc}"
    finally:
        if job is not None:
            job.close()
    parsed = parse_jsonl(raw, task_id, spec["objective"]) if raw.exists() else {"claim": None, "final_message": None, "usage": {"model": "unavailable", "input_tokens": "unavailable", "output_tokens": "unavailable"}, "malformed_lines": 0, "prompt_delivered": False}
    claim = parsed["claim"] if isinstance(parsed["claim"], dict) else {}
    if claim:
        write_json(folder / "worker_claim.json", claim)
    try:
        verification = {"status": "FAIL", "errors": ["TERMINATION_UNCONFIRMED"]} if termination_failed else verify(spec, folder / "verification_logs")
    except Exception as exc:
        verification = {"status": "FAIL", "errors": [f"VERIFIER_ERROR: {exc}"]}
    write_json(folder / "verification.json", verification)
    final_status = "TERMINATION_FAILED" if termination_failed else "TIMED_OUT" if timed_out else "COMPLETED" if exit_code == 0 and verification["status"] == "PASS" and parsed["prompt_delivered"] else "FAILED"
    warnings = ([failure] if failure else []) + ([] if parsed["prompt_delivered"] or exit_code is None else ["WORKER_PROMPT_NOT_DELIVERED"])
    result = {"task_id": task_id, "milestone": spec["objective"][:500], "worker_status": "TERMINATION_FAILED" if termination_failed else "TIMED_OUT" if timed_out else "EXITED" if exit_code is not None else "LAUNCH_FAILED", "status": final_status, "exit_code": exit_code, "prompt_delivered": parsed["prompt_delivered"], "worker_claim_status": str(claim.get("status", "unavailable"))[:30], "worker_summary": str(claim.get("summary") or parsed["final_message"] or "")[:500], "worker_files_changed": [str(p)[:200] for p in claim.get("files_changed", [])[:30]] if isinstance(claim.get("files_changed"), list) else [], "usage": parsed["usage"], "malformed_jsonl_lines": parsed["malformed_lines"], "verification": verification, "warnings": warnings, "artifacts": {"raw_jsonl": str(raw), "stderr": str(stderr), "verification": str(folder / "verification.json"), "worker_claim": str(folder / "worker_claim.json") if claim else None}}
    write_json(folder / "result.json", result)
    data.update(status=final_status, finished_at=now(), exit_code=exit_code)
    if termination_failed:
        data["finished_at"] = None
    write_json(folder / "status.json", data)
    if final_status in {"COMPLETED", "FAILED", "TIMED_OUT"}:
        release_worker_slot(task_id, base)


if __name__ == "__main__":
    run(sys.argv[1], Path(os.environ.get("AGENT_COMMANDER_STATE_ROOT", state_root())))
