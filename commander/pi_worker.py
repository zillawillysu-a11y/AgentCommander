import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from .config import ROOT, load_config
from .pi_parser import parse_jsonl
from .task_store import TASKS, read_json, status, task_dir, write_json
from .verifier import verify


def now():
    return datetime.now(timezone.utc).isoformat()


def prompt(task_id, spec):
    template = (ROOT / "prompts" / "pi_worker_en.md").read_text(encoding="utf-8")
    values = {"task_id": task_id, **spec}
    return template.replace("{{TASK}}", json.dumps(values, ensure_ascii=False, indent=2))


def launch(task_id, base=TASKS):
    folder = task_dir(task_id, base)
    runner = [sys.executable, "-m", "commander.pi_worker", task_id]
    env = os.environ.copy()
    env["AGENT_COMMANDER_TASKS"] = str(base)
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


def run(task_id, base=TASKS):
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
    command = [executable, "--mode", "json", "--print", "--no-session", "--", f"@{prompt_path}", "Complete the attached worker task. Follow its result contract."]
    write_json(folder / "metadata.json", {"invocation_flags": ["--mode", "json", "--print", "--no-session"], "fresh_session": True, "prompt_file": str(prompt_path), "project_root": spec["project_root"]})
    exit_code = None
    timed_out = False
    failure = None
    try:
        with raw.open("wb") as out, stderr.open("wb") as err:
            proc = subprocess.Popen(command, cwd=spec["project_root"], stdin=subprocess.DEVNULL, stdout=out, stderr=err)
            write_json(folder / "worker_process.json", {"pid": proc.pid, "started_at": now()})
            try:
                exit_code = proc.wait(timeout=spec["timeout_seconds"])
            except subprocess.TimeoutExpired:
                timed_out = True
                proc.kill()
                proc.wait()
    except OSError as exc:
        failure = f"Pi launch failed: {exc}"
    parsed = parse_jsonl(raw, task_id, spec["objective"]) if raw.exists() else {"claim": None, "final_message": None, "usage": {"model": "unavailable", "input_tokens": "unavailable", "output_tokens": "unavailable"}, "malformed_lines": 0, "prompt_delivered": False}
    claim = parsed["claim"] if isinstance(parsed["claim"], dict) else {}
    if claim:
        write_json(folder / "worker_claim.json", claim)
    try:
        verification = verify(spec, folder / "verification_logs")
    except Exception as exc:
        verification = {"status": "FAIL", "errors": [f"VERIFIER_ERROR: {exc}"]}
    write_json(folder / "verification.json", verification)
    final_status = "TIMED_OUT" if timed_out else "COMPLETED" if exit_code == 0 and verification["status"] == "PASS" and parsed["prompt_delivered"] else "FAILED"
    warnings = ([failure] if failure else []) + ([] if parsed["prompt_delivered"] or exit_code is None else ["WORKER_PROMPT_NOT_DELIVERED"])
    result = {"task_id": task_id, "milestone": spec["objective"][:500], "worker_status": "TIMED_OUT" if timed_out else "EXITED" if exit_code is not None else "LAUNCH_FAILED", "status": final_status, "exit_code": exit_code, "prompt_delivered": parsed["prompt_delivered"], "worker_claim_status": str(claim.get("status", "unavailable"))[:30], "worker_summary": str(claim.get("summary") or parsed["final_message"] or "")[:500], "worker_files_changed": [str(p)[:200] for p in claim.get("files_changed", [])[:30]] if isinstance(claim.get("files_changed"), list) else [], "usage": parsed["usage"], "malformed_jsonl_lines": parsed["malformed_lines"], "verification": verification, "warnings": warnings, "artifacts": {"raw_jsonl": str(raw), "stderr": str(stderr), "verification": str(folder / "verification.json"), "worker_claim": str(folder / "worker_claim.json") if claim else None}}
    write_json(folder / "result.json", result)
    data.update(status=final_status, finished_at=now(), exit_code=exit_code)
    write_json(folder / "status.json", data)


if __name__ == "__main__":
    run(sys.argv[1], Path(os.environ.get("AGENT_COMMANDER_TASKS", TASKS)))
