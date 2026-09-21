import fnmatch
import subprocess
from pathlib import Path


def bounded(text, lines=150, bytes_limit=16000):
    return "\n".join(text[-bytes_limit:].splitlines()[-lines:])


def file_tail(path, limit=16000):
    with Path(path).open("rb") as stream:
        stream.seek(0, 2)
        stream.seek(max(0, stream.tell() - limit))
        return bounded(stream.read().decode("utf-8", "replace"))


def command(argv, cwd, timeout=300, log_prefix=None):
    try:
        if log_prefix:
            prefix = Path(log_prefix)
            prefix.parent.mkdir(parents=True, exist_ok=True)
            stdout_path = prefix.with_suffix(".stdout.log")
            stderr_path = prefix.with_suffix(".stderr.log")
            with stdout_path.open("wb") as out, stderr_path.open("wb") as err:
                proc = subprocess.run(argv, cwd=cwd, stdout=out, stderr=err, timeout=timeout, shell=False)
            return {"exit_code": proc.returncode, "stdout_tail": file_tail(stdout_path), "stderr_tail": file_tail(stderr_path), "stdout_log": str(stdout_path), "stderr_log": str(stderr_path)}
        proc = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, errors="replace", timeout=timeout, shell=False)
        return {"exit_code": proc.returncode, "stdout_tail": bounded(proc.stdout), "stderr_tail": bounded(proc.stderr)}
    except (OSError, subprocess.TimeoutExpired) as exc:
        stdout_tail = file_tail(stdout_path) if log_prefix and stdout_path.exists() else ""
        stderr_tail = file_tail(stderr_path) if log_prefix and stderr_path.exists() else ""
        return {"exit_code": None, "stdout_tail": stdout_tail, "stderr_tail": bounded(stderr_tail + "\n" + str(exc))}


def changed_paths(root, baseline_head=None, head_exists=True):
    try:
        result = subprocess.run(["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"], cwd=root, stdin=subprocess.DEVNULL, capture_output=True, timeout=30)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("git status timed out after 30 seconds") from exc
    if result.returncode:
        raise RuntimeError("git status failed")
    entries = result.stdout.decode("utf-8", "replace").split("\0")
    paths = []
    index = 0
    while index < len(entries) and entries[index]:
        entry = entries[index]
        paths.append(entry[3:].replace("\\", "/"))
        index += 2 if entry[:2] in ("R ", " R", "C ", " C") else 1
    if baseline_head and head_exists:
        try:
            committed = subprocess.run(["git", "diff", "--name-only", "--no-renames", "-z", baseline_head, "HEAD"], cwd=root, stdin=subprocess.DEVNULL, capture_output=True, timeout=30)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("git baseline diff timed out after 30 seconds") from exc
        if committed.returncode:
            raise RuntimeError("git baseline diff failed")
        paths.extend(p.replace("\\", "/") for p in committed.stdout.decode("utf-8", "replace").split("\0") if p)
    return sorted(set(paths))


def allowed(path, patterns):
    path = path.replace("\\", "/")
    for pattern in patterns:
        pattern = pattern.replace("\\", "/").removeprefix("./").rstrip("/")
        if path == pattern or (not any(char in pattern for char in "*?[") and path.startswith(pattern + "/")):
            return True
        if pattern.endswith("/**") and (path == pattern[:-3] or path.startswith(pattern[:-2])):
            return True
        if fnmatch.fnmatchcase(path, pattern):
            return True
    return False


def verify(spec, log_dir=None):
    root = Path(spec["project_root"]).resolve()
    if not root.is_dir() or not (root / ".git").exists():
        return {"status": "FAIL", "errors": ["PROJECT_ROOT_INVALID"], "checks": []}
    errors = []
    baseline_head = spec.get("baseline_head")
    head_exists = command(["git", "rev-parse", "--verify", "HEAD"], root)["exit_code"] == 0
    if baseline_head and not spec.get("baseline_is_tree"):
        ancestor = command(["git", "merge-base", "--is-ancestor", baseline_head, "HEAD"], root)
        if ancestor["exit_code"] != 0:
            errors.append("BASELINE_NOT_ANCESTOR")
    paths = changed_paths(root, baseline_head, head_exists)
    preexisting = {p.replace("\\", "/") for p in spec.get("preexisting_paths", [])}
    outside = [p for p in paths if not allowed(p, spec["allowed_paths"])]
    preexisting_outside = [p for p in outside if p in preexisting]
    claimed = {p.replace("\\", "/") for p in spec.get("worker_claimed_paths", [])}
    attributable_outside = [p for p in outside if p not in preexisting]
    # In a shared worktree, a post-start forbidden path can only be attributed
    # to the Worker when it is present in the Worker's explicit file claim.
    # Otherwise report attribution as unknown instead of falsely blaming the
    # Worker for a concurrent Commander edit. Unknown attribution remains a
    # failed acceptance until the Commander reviews or isolates the change.
    if spec.get("shared_worktree"):
        worker_outside = [p for p in attributable_outside if p in claimed]
        attribution_unknown = [p for p in attributable_outside if p not in claimed]
    else:
        # Legacy/direct verifier callers have no concurrent Commander context;
        # preserve the original strict attribution behavior.
        worker_outside = attributable_outside
        attribution_unknown = []
    if worker_outside:
        errors.append("OUTSIDE_ALLOWED_PATHS")
    if attribution_unknown:
        errors.append("ATTRIBUTION_UNKNOWN")
    for relative in paths:
        target = (root / relative).resolve()
        if not target.is_relative_to(root):
            errors.append("OUTSIDE_PROJECT_SYMLINK")
    diff_check = command(["git", "diff", "--check"], root)
    staged_check = command(["git", "diff", "--cached", "--check"], root)
    baseline_check = command(["git", "diff", "--check", baseline_head, "HEAD"], root) if baseline_head and head_exists else {"exit_code": 0}
    if diff_check["exit_code"] or staged_check["exit_code"] or baseline_check["exit_code"]:
        errors.append("GIT_DIFF_CHECK_FAILED")
    stats = command(["git", "diff", baseline_head or "HEAD", "--numstat"], root)
    insertions = deletions = 0
    for line in stats["stdout_tail"].splitlines():
        columns = line.split("\t")
        if len(columns) >= 2:
            insertions += int(columns[0]) if columns[0].isdigit() else 0
            deletions += int(columns[1]) if columns[1].isdigit() else 0
    untracked = subprocess.run(["git", "ls-files", "--others", "--exclude-standard", "-z"], cwd=root, capture_output=True).stdout.decode("utf-8", "replace")
    # New files do not appear in git diff --numstat until staged.
    for relative in untracked.split("\0"):
        if relative:
            file = root / relative
            if file.is_file() and file.stat().st_size <= 1_000_000:
                try:
                    insertions += len(file.read_text(encoding="utf-8").splitlines())
                except (UnicodeError, OSError):
                    pass
    checks = []
    for relative in spec.get("required_files", []):
        exists = (root / relative).is_file()
        checks.append({"type": "required_file", "path": relative, "passed": exists})
        if not exists:
            errors.append("REQUIRED_FILE_MISSING")
    for index, argv in enumerate(spec.get("verification_commands", [])):
        if not isinstance(argv, list) or not argv or not all(isinstance(x, str) for x in argv):
            errors.append("INVALID_VERIFICATION_COMMAND")
            continue
        prefix = Path(log_dir) / f"command_{index + 1}" if log_dir else None
        outcome = command(argv, root, spec.get("verification_timeout_seconds", 300), prefix)
        checks.append({"type": "command", "argv": argv, **outcome})
        if outcome["exit_code"] != 0:
            errors.append("VERIFICATION_COMMAND_FAILED")
    return {"status": "PASS" if not errors else "FAIL", "errors": sorted(set(errors)), "changed_file_count": len(paths), "changed_paths": paths[:150], "outside_allowed_paths": worker_outside[:150], "preexisting_paths": sorted(preexisting)[:150], "preexisting_outside_paths": preexisting_outside[:150], "attribution_unknown_paths": attribution_unknown[:150], "path_validation": {"status": "PASS" if not worker_outside and not attribution_unknown else "FAIL", "worker_paths": [p for p in paths if p not in preexisting], "commander_or_existing_paths": [p for p in paths if p in preexisting], "outside_allowed_paths": worker_outside[:150], "attribution_unknown_paths": attribution_unknown[:150]}, "insertions": insertions, "deletions": deletions, "diff_check": diff_check, "checks": checks}
