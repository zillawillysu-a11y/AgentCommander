"""Create matched Direct-vs-AgentCommander benchmark projects."""
from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

PROMPT = """Implement the todo persistence milestone in this repository.

Requirements:
- Implement src/todo.py with a TodoStore backed by a JSON file.
- Support add(title), list_items(), and complete(item_id), with stable integer IDs.
- Add src/todo_cli.py with add, list, and complete commands.
- Add deterministic tests for persistence, completion, and CLI behavior.
- Keep it dependency-free, update README.md with usage, and run the full test suite.
"""

SOURCE = {
    "src/__init__.py": "",
    "src/todo.py": "# TODO: implement TodoStore\n",
    "src/todo_cli.py": "# TODO: implement CLI\n",
    "tests/test_todo.py": "def test_placeholder():\n    assert True\n",
    "README.md": "# Todo benchmark\n\nThis repository is intentionally incomplete.\n",
}


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def create_project(path: Path, label: str) -> None:
    path.mkdir(parents=True)
    for relative, content in SOURCE.items():
        target = path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    (path / "BENCHMARK_PROMPT.md").write_text(PROMPT, encoding="utf-8")
    (path / "BENCHMARK_RESULT.json").write_text(json.dumps({
        "label": label,
        "started_at": None,
        "finished_at": None,
        "codex_usage": "fill from Codex client; unavailable if not shown",
        "codex_repairs": None,
        "tests": None,
        "quality_notes": "",
        "agentcommander_task_id": None,
        "worker_metrics": "copy cost_metrics.worker from get_task_result when applicable",
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    git(path, "init", "-q")
    git(path, "add", ".")
    subprocess.run(["git", "-c", "user.name=AgentCommander benchmark", "-c", "user.email=benchmark@localhost", "commit", "-qm", "seed"], cwd=path, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare a matched Direct-vs-AgentCommander benchmark")
    parser.add_argument("--output", type=Path, help="parent directory (default: temporary directory)")
    args = parser.parse_args()
    parent = args.output or Path(tempfile.mkdtemp(prefix="agentcommander-benchmark-"))
    root = parent / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    direct = root / "direct"
    delegated = root / "agentcommander"
    create_project(direct, "direct")
    create_project(delegated, "agentcommander")
    manifest = {"created_at": datetime.now(timezone.utc).isoformat(), "prompt_file": "BENCHMARK_PROMPT.md", "projects": {"direct": str(direct), "agentcommander": str(delegated)}, "comparison_rule": "same prompt, same acceptance, compare quality before cost"}
    (root / "BENCHMARK_MANIFEST.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(root)
    print("Run the same BENCHMARK_PROMPT.md in direct (no delegation) and agentcommander (AUTO).")
    print("Fill each BENCHMARK_RESULT.json with Codex usage, tests, quality, and Worker metrics.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
