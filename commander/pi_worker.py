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


def elapsed_seconds(started_at, finished_at):
    try:
        return max(0.0, (datetime.fromisoformat(finished_at) - datetime.fromisoformat(started_at)).total_seconds())
    except (TypeError, ValueError):
        return "unavailable"


def consume_output_usage(path, offset=0, pending=b""):
    """Read newly completed Pi JSONL records and return assistant output usage."""
    if not Path(path).exists():
        return 0, offset, pending
    with Path(path).open("rb") as stream:
        stream.seek(offset)
        chunk = stream.read()
        offset = stream.tell()
    lines = (pending + chunk).split(b"\n")
    pending = lines.pop() if lines else b""
    output = 0
    for line in lines:
        try:
            event = json.loads(line.decode("utf-8", "replace"))
            message = event.get("message", {})
            usage = message.get("usage", {}) if isinstance(message, dict) else {}
            if event.get("type") == "message_end" and message.get("role") == "assistant" and isinstance(usage.get("output"), int):
                output += usage["output"]
        except (json.JSONDecodeError, AttributeError):
            continue
    return output, offset, pending


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
    budget_reached = False
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
            usage_offset = observed_output = 0
            usage_pending = b""
            while proc.poll() is None and __import__("time").monotonic() < deadline:
                moment = __import__("time").monotonic()
                if moment - last_snapshot >= 1:
                    added, usage_offset, usage_pending = consume_output_usage(raw, usage_offset, usage_pending)
                    observed_output += added
                    record["descendants"] = merge_identities(record["descendants"], capture_tree(record["pi_root"]))
                    record["observed_output_tokens"] = observed_output
                    record["last_observed_at"] = now()
                    write_json(folder / "worker_process.json", record)
                    last_snapshot = moment
                    limit = spec.get("max_output_tokens")
                    if isinstance(limit, int) and observed_output >= limit:
                        budget_reached = True
                        record["termination_reason"] = "OUTPUT_LIMIT_REACHED"
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
                        break
                __import__("time").sleep(0.1)
            if proc.poll() is None and not budget_reached:
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
            elif not budget_reached:
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
    worker_files = [str(p).replace("\\", "/")[:200] for p in claim.get("files_changed", [])[:30]] if isinstance(claim.get("files_changed"), list) else []
    if claim:
        write_json(folder / "worker_claim.json", claim)
    try:
        verification_spec = {**spec, "worker_claimed_paths": worker_files}
        verification = {"status": "FAIL", "errors": ["TERMINATION_UNCONFIRMED"]} if termination_failed else verify(verification_spec, folder / "verification_logs")
    except Exception as exc:
        verification = {"status": "FAIL", "errors": [f"VERIFIER_ERROR: {exc}"]}
    write_json(folder / "verification.json", verification)
    output_limit = spec.get("max_output_tokens")
    output_value = parsed.get("usage", {}).get("output_tokens")
    output_limited = budget_reached or (isinstance(output_limit, int) and isinstance(output_value, int) and output_value >= output_limit)
    final_status = "TERMINATION_FAILED" if termination_failed else "TIMED_OUT" if timed_out else "OUTPUT_LIMIT_REACHED" if output_limited and verification["status"] == "PASS" and parsed["prompt_delivered"] else "COMPLETED" if exit_code == 0 and verification["status"] == "PASS" and parsed["prompt_delivered"] else "FAILED"
    warnings = ([failure] if failure else []) + ([] if parsed["prompt_delivered"] or exit_code is None else ["WORKER_PROMPT_NOT_DELIVERED"])
    finished_at = now()
    test_checks = verification.get("checks", []) if isinstance(verification, dict) else []
    tests_status = "PASS" if test_checks and all(item.get("exit_code") == 0 for item in test_checks if item.get("type") == "command") else "FAIL" if any(item.get("type") == "command" and item.get("exit_code") != 0 for item in test_checks) else "UNAVAILABLE"
    worker_process_status = "TERMINATION_FAILED" if termination_failed else "TIMED_OUT" if timed_out else "BUDGET_STOPPED" if budget_reached else "EXITED" if exit_code is not None else "LAUNCH_FAILED"
    if output_limited:
        warnings.append(f"OUTPUT_LIMIT_REACHED:{output_limit}")
    acceptance_reasons = list(verification.get("errors", [])) + (["OUTPUT_LIMIT_REACHED"] if output_limited else [])
    result = {"task_id": task_id, "milestone": spec["objective"][:500], "worker_status": worker_process_status, "status": final_status, "exit_code": exit_code, "prompt_delivered": parsed["prompt_delivered"], "worker_claim_status": str(claim.get("status", "unavailable"))[:30], "worker_summary": str(claim.get("summary") or parsed["final_message"] or "")[:500], "worker_files_changed": worker_files, "usage": parsed["usage"], "malformed_jsonl_lines": parsed["malformed_lines"], "verification": verification, "worker_process_result": {"status": worker_process_status, "exit_code": exit_code}, "worker_claim_result": {"status": str(claim.get("status", "unavailable")), "summary": str(claim.get("summary") or "")[:500]}, "test_result": {"status": tests_status, "checks": test_checks}, "path_validation": verification.get("path_validation", {"status": "UNAVAILABLE"}), "final_acceptance": {"status": final_status, "reasons": acceptance_reasons}, "budget": {"max_output_tokens": output_limit, "output_tokens": output_value, "status": "REACHED" if output_limited else "NOT_REACHED" if output_limit else "UNCONFIGURED", "partial_result_preserved": bool(output_limited)}, "cost_metrics": {"task_scope": spec.get("task_scope", "UNKNOWN"), "repair_count": 1 if spec.get("repair_of") else 0, "worker": {"usage": parsed["usage"], "elapsed_seconds": elapsed_seconds(data.get("started_at"), finished_at)}, "codex_commander": {"status": "UNAVAILABLE", "planning": "unavailable", "waiting": "unavailable", "tool_calls": "unavailable", "review": "unavailable", "repairs": "unavailable"}, "comparison": "unavailable_without_comparable_codex_baseline"}, "warnings": warnings, "artifacts": {"raw_jsonl": str(raw), "stderr": str(stderr), "verification": str(folder / "verification.json"), "worker_claim": str(folder / "worker_claim.json") if claim else None}}
    write_json(folder / "result.json", result)
    data.update(status=final_status, finished_at=finished_at, exit_code=exit_code)
    if termination_failed:
        data["finished_at"] = None
    write_json(folder / "status.json", data)
    if final_status in {"COMPLETED", "FAILED", "TIMED_OUT", "OUTPUT_LIMIT_REACHED"}:
        release_worker_slot(task_id, base)


if __name__ == "__main__":
    run(sys.argv[1], Path(os.environ.get("AGENT_COMMANDER_STATE_ROOT", state_root())))
