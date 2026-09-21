import json
import os
import shutil
import subprocess
import sys
import hashlib
from datetime import datetime, timezone
from pathlib import Path

from .config import DEFAULT, ROOT, load_config
from .completion import record_completion_event, update_completion_report
from .entrypoint import worker_command
from .models import pi_model_args
from .loop_guard import LoopGuard
from .orca_notify import send_completion_to_orca
from .pi_parser import parse_jsonl
from .process_control import WindowsJob, capture_tree, identity, merge_identities, owned_alive, terminate_task
from .subprocess_utils import background_creation_flags, hidden_run_kwargs
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


def consume_runtime_events(path, offset=0, pending=b""):
    """Read complete JSONL events without assuming line arrival boundaries."""
    if not Path(path).exists():
        return [], 0, pending
    with Path(path).open("rb") as stream:
        stream.seek(offset); chunk = stream.read(); offset = stream.tell()
    lines = (pending + chunk).split(b"\n"); pending = lines.pop() if lines else b""
    events = []
    for line in lines:
        try:
            event = json.loads(line.decode("utf-8", "replace"))
            if isinstance(event, dict): events.append(event)
        except json.JSONDecodeError:
            continue
    return events, offset, pending


def repository_progress_fingerprint(root):
    """Cheap content-aware progress signal for tracked and untracked changes."""
    status = subprocess.run(["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"], cwd=root, stdin=subprocess.DEVNULL, capture_output=True, timeout=30, **hidden_run_kwargs()).stdout
    diff = subprocess.run(["git", "diff", "--binary", "HEAD"], cwd=root, stdin=subprocess.DEVNULL, capture_output=True, timeout=30, **hidden_run_kwargs()).stdout
    return hashlib.sha256(status + b"\0" + diff).hexdigest()


def observe_runtime_events(guard, events, project_root):
    """Feed a new event batch to the guard, fingerprinting only when needed."""
    if guard is None or not events:
        return None
    try:
        progress = repository_progress_fingerprint(project_root)
    except Exception:
        progress = None
    for event in events:
        stop_reason = guard.observe(event, progress)
        if stop_reason:
            return stop_reason
    return None


def prompt(task_id, spec):
    template = (ROOT / "prompts" / "pi_worker_en.md").read_text(encoding="utf-8")
    values = {"task_id": task_id, **spec}
    return template.replace("{{TASK}}", json.dumps(values, ensure_ascii=False, indent=2))


MECHANICAL_ERRORS = {"VERIFICATION_COMMAND_FAILED", "GIT_DIFF_CHECK_FAILED", "REQUIRED_FILE_MISSING"}
UNSAFE_REPAIR_ERRORS = {"OUTSIDE_ALLOWED_PATHS", "ATTRIBUTION_UNKNOWN", "BASELINE_NOT_ANCESTOR", "OUTSIDE_PROJECT_SYMLINK", "INVALID_VERIFICATION_COMMAND", "PROJECT_ROOT_INVALID", "VERIFICATION_SIDE_EFFECT", "DUPLICATE_TOOL_CALL", "REPEATED_FAILURE", "CYCLIC_TOOL_PATTERN", "IDEMPOTENT_NO_PROGRESS", "NO_PROGRESS", "TOOL_BUDGET_REACHED"}
MUTATING_TASK_KINDS = {"IMPLEMENT", "REFACTOR", "TEST"}


def is_mechanical_failure(verification):
    errors = set(verification.get("errors", []))
    return bool(errors & MECHANICAL_ERRORS) and not bool(errors & UNSAFE_REPAIR_ERRORS)


def acceptance_issues(spec, parsed, verification):
    """Return non-verification reasons that prevent final task acceptance."""
    issues = []
    claim = parsed.get("claim") if isinstance(parsed.get("claim"), dict) else None
    if parsed.get("final_stop_reason") == "length":
        issues.append("WORKER_OUTPUT_TRUNCATED")
    if not claim:
        issues.append("WORKER_CLAIM_MISSING")
    elif str(claim.get("status", "")).upper() != "PASS":
        issues.append("WORKER_CLAIM_NOT_PASS")
    if str(spec.get("task_kind", "IMPLEMENT")).upper() in MUTATING_TASK_KINDS:
        path_validation = verification.get("path_validation", {}) if isinstance(verification, dict) else {}
        worker_paths = path_validation.get("worker_paths", [])
        if not worker_paths:
            issues.append("NO_IMPLEMENTATION_CHANGE")
    return issues


def final_acceptance_status(*, termination_failed, timed_out, guard_stopped, output_limited, exit_code, verification_passed, prompt_delivered, contract_issues):
    if termination_failed:
        return "TERMINATION_FAILED"
    if timed_out:
        return "TIMED_OUT"
    if guard_stopped:
        return "LOOP_GUARD_STOPPED"
    if output_limited and verification_passed and prompt_delivered:
        return "OUTPUT_LIMIT_REACHED"
    if exit_code == 0 and verification_passed and prompt_delivered:
        return "PARTIAL" if contract_issues else "COMPLETED"
    return "FAILED"


def repair_prompt(task_id, spec, verification, changed_files, attempt):
    failed = next((item for item in verification.get("checks", []) if item.get("type") == "command" and item.get("exit_code") != 0), {})
    contract = {"task_id": task_id, "repair_attempt": attempt, "objective": spec["objective"], "acceptance_criteria": spec["acceptance_criteria"], "allowed_paths": spec["allowed_paths"], "changed_files": changed_files, "failing_command": failed.get("argv"), "stdout_tail": str(failed.get("stdout_tail", ""))[-3000:], "stderr_tail": str(failed.get("stderr_tail", ""))[-3000:], "verification_errors": verification.get("errors", [])}
    return "You are a fresh local repair Worker. Fix only the mechanical failure in this compact contract. Do not redesign, refactor unrelated code, add dependencies, change public APIs, commit, switch branches, or edit outside allowed_paths. Run the failing check and stop. Return compact JSON.\n\n" + json.dumps(contract, ensure_ascii=False, indent=2)


def run_local_repair(task_id, spec, folder, executable, model_args, verification, attempt, guard_settings):
    """Run one fresh --no-session Pi process; return process and guard facts."""
    repair_dir = folder / "repairs" / str(attempt); repair_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = repair_dir / "prompt.md"
    try: changed = __import__("commander.verifier", fromlist=["changed_paths"]).changed_paths(spec["project_root"], spec.get("baseline_head"))
    except Exception: changed = []
    prompt_path.write_text(repair_prompt(task_id, spec, verification, changed, attempt), encoding="utf-8")
    raw, err = repair_dir / "worker.jsonl", repair_dir / "worker.stderr.log"
    command = [executable, *model_args, "--mode", "json", "--print", "--no-session", "--", f"@{prompt_path}", "Repair the attached mechanical failure only."]
    with raw.open("wb") as out, err.open("wb") as stderr_stream:
        kwargs = {"cwd": spec["project_root"], "stdin": subprocess.DEVNULL, "stdout": out, "stderr": stderr_stream}
        if os.name == "nt": kwargs["creationflags"] = background_creation_flags(new_process_group=True)
        else: kwargs["start_new_session"] = True
        proc = subprocess.Popen(command, **kwargs); job = WindowsJob(proc)
        deadline = __import__("time").monotonic() + min(1800, spec.get("timeout_seconds", 1800))
        event_offset = 0; event_pending = b""; repair_guard = LoopGuard(guard_settings, "SMALL"); repair_stop = None
        while proc.poll() is None and __import__("time").monotonic() < deadline:
            events, event_offset, event_pending = consume_runtime_events(raw, event_offset, event_pending)
            repair_stop = observe_runtime_events(repair_guard, events, spec["project_root"])
            if repair_stop: break
            __import__("time").sleep(.1)
        if proc.poll() is None:
            if job.assigned: job.close()
            try: proc.kill(); proc.wait(timeout=5)
            except Exception: pass
        exit_code = proc.returncode
        job.close()
    return {"attempt": attempt, "fresh_session": True, "exit_code": exit_code, "stop_reason": repair_stop, "loop_guard": repair_guard.diagnostics(), "raw_jsonl": str(raw), "stderr": str(err)}


def launch(task_id, base=None):
    base = Path(base) if base is not None else state_root()
    folder = task_dir(task_id, base)
    runner = worker_command(task_id)
    env = os.environ.copy()
    env["AGENT_COMMANDER_STATE_ROOT"] = str(base)
    kwargs = {"cwd": str(ROOT), "env": env, "stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if os.name == "nt":
        kwargs["creationflags"] = background_creation_flags(new_process_group=True, detached=True)
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
    guard_stopped = False
    stop_reason = None
    termination_failed = False
    failure = None
    job = None
    try:
        with raw.open("wb") as out, stderr.open("wb") as err:
            kwargs = {"cwd": spec["project_root"], "stdin": subprocess.DEVNULL, "stdout": out, "stderr": err}
            if os.name == "nt":
                kwargs["creationflags"] = background_creation_flags(new_process_group=True)
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
            event_offset = 0
            event_pending = b""
            guard_settings = config.get("guard", DEFAULT["guard"])
            guard = LoopGuard(guard_settings, spec.get("task_scope", "NORMAL")) if guard_settings.get("enabled", True) else None
            while proc.poll() is None and __import__("time").monotonic() < deadline:
                moment = __import__("time").monotonic()
                if moment - last_snapshot >= 1:
                    added, usage_offset, usage_pending = consume_output_usage(raw, usage_offset, usage_pending)
                    observed_output += added
                    events, event_offset, event_pending = consume_runtime_events(raw, event_offset, event_pending)
                    stop_reason = observe_runtime_events(guard, events, spec["project_root"])
                    record["descendants"] = merge_identities(record["descendants"], capture_tree(record["pi_root"]))
                    record["observed_output_tokens"] = observed_output
                    record["last_observed_at"] = now()
                    write_json(folder / "worker_process.json", record)
                    last_snapshot = moment
                    limit = spec.get("max_output_tokens")
                    if stop_reason:
                        guard_stopped = True
                        record["termination_reason"] = stop_reason
                        write_json(folder / "worker_process.json", record)
                        if job.assigned: job.close()
                        termination = terminate_task(task_id, base)
                        termination_failed = not termination["confirmed_gone"]
                        if not termination_failed:
                            try: exit_code = proc.wait(timeout=5)
                            except subprocess.TimeoutExpired: termination_failed = True
                        break
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
            if proc.poll() is None and not budget_reached and not guard_stopped:
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
            elif not budget_reached and not guard_stopped:
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
    parsed = parse_jsonl(raw, task_id, spec["objective"]) if raw.exists() else {"claim": None, "final_message": None, "final_stop_reason": None, "usage": {"model": "unavailable", "input_tokens": "unavailable", "output_tokens": "unavailable"}, "malformed_lines": 0, "prompt_delivered": False}
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
    repair_history = []
    if not termination_failed and not timed_out and not budget_reached and not guard_stopped and parsed["prompt_delivered"] and is_mechanical_failure(verification):
        for attempt in range(1, int(config.get("pi", {}).get("max_repairs", 2)) + 1):
            repair = run_local_repair(task_id, spec, folder, executable, model_args, verification, attempt, config.get("guard", DEFAULT["guard"]))
            repair_verification = verify({**spec, "worker_claimed_paths": []}, folder / "verification_logs" / f"repair_{attempt}")
            repair["verification_status"] = repair_verification["status"]
            repair["verification_errors"] = repair_verification.get("errors", [])
            repair_history.append(repair); verification = repair_verification
            write_json(folder / "verification.json", verification)
            if repair.get("stop_reason"):
                stop_reason = repair["stop_reason"]
                verification["errors"] = sorted(set(verification.get("errors", [])) | {stop_reason})
                break
            if verification["status"] == "PASS" or not is_mechanical_failure(verification): break
    output_limit = spec.get("max_output_tokens")
    output_value = parsed.get("usage", {}).get("output_tokens")
    output_limited = budget_reached or (isinstance(output_limit, int) and isinstance(output_value, int) and output_value >= output_limit)
    contract_issues = acceptance_issues(spec, parsed, verification)
    final_status = final_acceptance_status(termination_failed=termination_failed, timed_out=timed_out, guard_stopped=guard_stopped, output_limited=output_limited, exit_code=exit_code, verification_passed=verification["status"] == "PASS", prompt_delivered=parsed["prompt_delivered"], contract_issues=contract_issues)
    warnings = ([failure] if failure else []) + ([] if parsed["prompt_delivered"] or exit_code is None else ["WORKER_PROMPT_NOT_DELIVERED"])
    finished_at = now()
    test_checks = verification.get("checks", []) if isinstance(verification, dict) else []
    tests_status = "PASS" if test_checks and all(item.get("exit_code") == 0 for item in test_checks if item.get("type") == "command") else "FAIL" if any(item.get("type") == "command" and item.get("exit_code") != 0 for item in test_checks) else "UNAVAILABLE"
    worker_process_status = "TERMINATION_FAILED" if termination_failed else "TIMED_OUT" if timed_out else "GUARD_STOPPED" if guard_stopped else "BUDGET_STOPPED" if budget_reached else "EXITED" if exit_code is not None else "LAUNCH_FAILED"
    if output_limited:
        warnings.append(f"OUTPUT_LIMIT_REACHED:{output_limit}")
    acceptance_reasons = list(verification.get("errors", [])) + (["OUTPUT_LIMIT_REACHED"] if output_limited else []) + contract_issues
    final_stop_reason = "REPAIR_LIMIT_REACHED" if repair_history and verification["status"] != "PASS" and is_mechanical_failure(verification) else stop_reason or (contract_issues[0] if contract_issues else None)
    safe_summary = str(claim.get("summary") or "")[:500] if claim else "Worker did not return a valid structured result."
    result = {"task_id": task_id, "milestone": spec["objective"][:500], "worker_status": worker_process_status, "status": final_status, "stop_reason": final_stop_reason, "exit_code": exit_code, "prompt_delivered": parsed["prompt_delivered"], "worker_claim_status": str(claim.get("status", "unavailable"))[:30], "worker_summary": safe_summary, "worker_files_changed": worker_files, "usage": parsed["usage"], "malformed_jsonl_lines": parsed["malformed_lines"], "verification": verification, "loop_guard": guard.diagnostics() if 'guard' in locals() and guard else None, "repair_history": repair_history, "worker_process_result": {"status": worker_process_status, "exit_code": exit_code, "assistant_stop_reason": parsed.get("final_stop_reason")}, "worker_claim_result": {"status": str(claim.get("status", "unavailable")), "summary": str(claim.get("summary") or "")[:500]}, "test_result": {"status": tests_status}, "path_validation": verification.get("path_validation", {"status": "UNAVAILABLE"}), "final_acceptance": {"status": final_status, "reasons": acceptance_reasons}, "budget": {"max_output_tokens": output_limit, "output_tokens": output_value, "status": "REACHED" if output_limited else "NOT_REACHED" if output_limit else "UNCONFIGURED", "partial_result_preserved": bool(output_limited or guard_stopped or final_status == "PARTIAL")}, "cost_metrics": {"task_scope": spec.get("task_scope", "UNKNOWN"), "repair_count": len(repair_history) + (1 if spec.get("repair_of") else 0), "loop_guard_warnings": len(guard.warnings) if 'guard' in locals() and guard else 0, "loop_guard_stops": 1 if guard_stopped else 0, "worker": {"usage": parsed["usage"], "elapsed_seconds": elapsed_seconds(data.get("started_at"), finished_at)}, "codex_commander": {"status": "UNAVAILABLE", "planning": "unavailable", "waiting": "unavailable", "tool_calls": "unavailable", "review": "unavailable", "repairs": "unavailable"}, "comparison": "unavailable_without_comparable_codex_baseline"}, "warnings": warnings, "artifacts": {"raw_jsonl": str(raw), "stderr": str(stderr), "verification": str(folder / "verification.json"), "worker_claim": str(folder / "worker_claim.json") if claim else None}}
    report_path = None
    report_error = None
    try:
        report_path = update_completion_report(spec, result, data.get("started_at"), finished_at)
    except Exception as exc:
        report_error = f"{type(exc).__name__}: {exc}"
        result["warnings"].append(f"COMPLETION_REPORT_WRITE_FAILED: {report_error}")
        result["status"] = "PARTIAL"
        result["stop_reason"] = "COMPLETION_REPORT_WRITE_FAILED"
        result["final_acceptance"]["status"] = "PARTIAL"
        result["final_acceptance"]["reasons"].append("COMPLETION_REPORT_WRITE_FAILED")
    write_json(folder / "result.json", result)
    record_completion_event(spec, result, finished_at, report_path, report_error, base)
    data.update(status=result["status"], finished_at=finished_at, exit_code=exit_code)
    if termination_failed:
        data["finished_at"] = None
    write_json(folder / "status.json", data)
    if final_status in {"COMPLETED", "PARTIAL", "FAILED", "TIMED_OUT", "OUTPUT_LIMIT_REACHED", "LOOP_GUARD_STOPPED"}:
        release_worker_slot(task_id, base)
    delivery = send_completion_to_orca(spec, result)
    event_path = Path(base) / "state" / "completion_events" / f"{task_id}.json"
    if event_path.exists():
        event = read_json(event_path)
        event["orca_delivery"] = delivery
        write_json(event_path, event)


if __name__ == "__main__":
    run(sys.argv[1], Path(os.environ.get("AGENT_COMMANDER_STATE_ROOT", state_root())))
