"""Create matched Direct-vs-AgentCommander benchmark projects."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from commander.benchmark import prepare


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare a matched Direct-vs-AgentCommander benchmark")
    parser.add_argument("--output", type=Path, help="parent directory (default: temporary directory)")
    args = parser.parse_args()
    root = prepare(args.output or Path.cwd() / ".tmp" / "agentcommander-benchmark")
    print(root)
    print("Run the same BENCHMARK_PROMPT.md in direct (no delegation) and agentcommander (AUTO).")
    print("Fill each BENCHMARK_RESULT.json with Codex usage, tests, quality, and Worker metrics.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
