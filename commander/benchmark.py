"""Disposable matched Direct-vs-AgentCommander benchmark project generator."""
from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

PROMPT = """Implement the task tracker persistence milestone in this repository.

Requirements:
- Implement src/todo.py with a dependency-free JSON-backed TodoStore.
- Support add, list/filter, complete, reopen, delete, and summary operations.
- Preserve stable integer IDs across deletes and process/store reloads.
- Validate malformed JSON and invalid records with clear domain errors.
- Use atomic writes so a failed replacement cannot corrupt the existing store.
- Implement src/todo_cli.py with add, list, complete, reopen, delete, and summary commands.
- Add JSON export and import in src/todo_exchange.py; imports must validate all records before changing the store.
- Provide useful exit codes and deterministic text/JSON output.
- Add comprehensive deterministic tests for storage, validation, CLI, import/export, and failure behavior.
- Keep it dependency-free, update README.md with examples, and run the full test suite.
"""
SOURCE = {"src/__init__.py": "", "src/todo.py": "# TODO: implement TodoStore\n", "src/todo_cli.py": "# TODO: implement CLI\n", "src/todo_exchange.py": "# TODO: implement validated import/export\n", "tests/test_todo.py": "def test_placeholder():\n    assert True\n", "README.md": "# Task tracker benchmark\n\nThis repository is intentionally incomplete.\n"}


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _create_project(path: Path, label: str) -> None:
    path.mkdir(parents=True)
    for relative, content in SOURCE.items():
        target = path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    (path / "BENCHMARK_PROMPT.md").write_text(PROMPT, encoding="utf-8")
    (path / "BENCHMARK_RESULT.json").write_text(json.dumps({"schema_version": 2, "label": label, "run_id": None, "started_at": None, "finished_at": None, "elapsed_seconds": None, "codex_usage": "fill from Codex client; unavailable if not shown", "codex_repairs": None, "tests": None, "quality_notes": "", "agentcommander_task_id": None, "worker_metrics": {"runtime_seconds": None, "input_tokens": None, "output_tokens": None, "cache_read_tokens": None, "cache_write_tokens": None, "local_repairs": None, "loop_guard_warnings": None, "loop_guard_stops": None, "stop_reason": None}, "commander_overhead_ratio": "calculate only from comparable client-reported Codex usage"}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    _git(path, "init", "-q")
    _git(path, "add", ".")
    subprocess.run(["git", "-c", "user.name=AgentCommander benchmark", "-c", "user.email=benchmark@localhost", "commit", "-qm", "seed"], cwd=path, check=True)


def prepare(parent: Path) -> Path:
    parent.mkdir(parents=True, exist_ok=True)
    root = parent / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    direct, delegated = root / "direct", root / "agentcommander"
    _create_project(direct, "direct")
    _create_project(delegated, "agentcommander")
    manifest = {"created_at": datetime.now(timezone.utc).isoformat(), "prompt_file": "BENCHMARK_PROMPT.md", "projects": {"direct": str(direct), "agentcommander": str(delegated)}, "comparison_rule": "same prompt, same acceptance, compare quality before cost"}
    root.mkdir(exist_ok=True)
    (root / "BENCHMARK_MANIFEST.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return root
