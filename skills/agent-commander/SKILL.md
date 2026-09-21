---
name: agent-commander
description: Delegate bounded repository work through the AgentCommander MCP when local Worker execution is likely to reduce Commander effort; use for substantial mechanical implementation, tests, searches, migrations, and refactors, not trivial edits or architectural decisions.
---

# AgentCommander

Remain the planner, reviewer, and final authority. Pi/Qwen implements a compact contract in a fresh session. The user's explicit request overrides AUTO and FORCE.

## Delegation gate

Check `get_agentcommander_status` when delegation is relevant. OFF never delegates. AUTO delegates only when expected implementation, search, or test work clearly exceeds dispatch and review overhead. FORCE strongly prefers delegation for bounded coding implementation, but not for trivial edits, ambiguous requirements, or architecture decisions.

Use direct Codex work for obvious one-file or syntax fixes and judgment-heavy design. Prefer delegation for repetitive changes, bounded tests, mechanical refactors and migrations, repository search, clearly specified modules, and repeated local test/fix work.

## Task contract

Set one task kind: IMPLEMENT, REFACTOR, TEST, DIAGNOSE, SEARCH, or REVIEW. DIAGNOSE, SEARCH, and REVIEW are read-only by default. Specify acceptance criteria, narrow `allowed_paths`, deterministic verification commands, and only useful context. Codex owns architecture and public API decisions.

Use SMALL for small or repair work, NORMAL for ordinary substantial work, and LARGE only for a clearly large milestone. Never report that a Worker started until delegation returns a task ID and RUNNING or QUEUED.

## Result workflow

Do not poll repeatedly. For a long RUNNING task, return the task ID and end the turn. On return, read `get_task_result` first. Accept a verified successful milestone without re-reading the repository. Request `get_task_diagnostics` only for PARTIAL or FAILED work requiring judgment.

AgentCommander may perform bounded fresh-session local repairs for mechanical test, lint, syntax, required-file, or clear acceptance failures. It escalates unsafe path, attribution, baseline, process, prompt, dependency, API, architecture, ambiguity, repeated loop-stop, and exhausted-repair failures.

Keep Worker runtime and tokens separate from Codex usage. Benchmark targets never override correctness, quality, safety, or sensible delegation.
