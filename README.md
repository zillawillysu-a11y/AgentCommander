# AgentCommander 0.2.0

## Bounded delegation protocol

`delegate_pi` accepts a `task_kind`: `IMPLEMENT`, `REFACTOR`, `TEST`, `DIAGNOSE`, `SEARCH`, or `REVIEW`. Read-only kinds are read-only by default; every task still uses explicit acceptance criteria, `allowed_paths`, a Git baseline, and deterministic verification. The Worker contract requires the smallest valid patch and forbids opportunistic cleanup, architecture redesign, dependency additions, public API changes, and scope expansion unless explicitly authorized.

`get_task_result` is the compact Commander response. A successful response contains only acceptance state, summary, changed files, test counts, repair count, elapsed time, basic Worker usage, and stop reason; stdout, stderr, detailed checks, path attribution, and artifact paths stay on local disk. Call `get_task_diagnostics` explicitly for a failed or partial task that needs investigation.

The runtime watchdog consumes Pi's JSONL `tool_execution_start` and `tool_execution_end` events. It detects duplicate calls, equivalent repeated failures, short 2-4 action cycles, repeated idempotent reads, no-progress windows, and scope-specific tool budgets. Defaults warn/stop at 2/3 identical actions, warn/stop at 2/3 cycle laps, stop after 20 no-progress actions, and allow 40/80/140 tool actions for SMALL/NORMAL/LARGE. A guard stop preserves the worktree and logs, terminates the owned process tree, and runs deterministic verification when safe.

On Windows, background Worker, watchdog Git, verification, repair, and termination subprocesses use no-window creation flags so the portable application does not repeatedly open console windows. Verification ignores only recognized disposable Python execution artifacts (`__pycache__`, `.pyc`, `.pyo`, and `.pytest_cache`) for source attribution, reports them separately in diagnostics, and still rejects other undeclared paths or non-generated verification side effects.

Mechanical verification failures (targeted command, diff-check, or required-file failures without a safety error) receive up to two local repair attempts by default. Each repair is a fresh `--no-session` Worker with only a compact objective, acceptance criteria, allowed paths, changed files, failing command, and bounded output tails. Path attribution, baseline, process lifecycle, prompt delivery, dependency/API/architecture decisions, ambiguity, repeated guard stops, and exhausted repairs escalate to the Commander.

Final acceptance is separate from test execution. Mutating IMPLEMENT, REFACTOR, and TEST tasks cannot complete with zero Worker-attributed changes; a missing structured Worker claim or an assistant response truncated by the model output limit produces PARTIAL even when existing verification commands pass. Diagnostics expose these acceptance reasons without copying unfinished reasoning or code into the compact result.

Codex integration installs a small discovery/safety block in the global `AGENTS.md` and the detailed on-demand Skill at `~/.agents/skills/agent-commander/SKILL.md`. Install, update, and removal touch only AgentCommander's managed block and managed Skill file. AUTO uses an inspectable delegation gate: delegate bounded substantial or repetitive work only when it is likely to save Commander effort; direct Codex remains preferable for trivial edits and architecture decisions. FORCE strongly prefers suitable implementation delegation, while OFF blocks it.

AgentCommander 讓 Codex 擔任 Commander，並在適合時透過 MCP 把實作里程碑交給 Pi / Local Qwen。Codex 保留規劃、審查、deterministic verification 與最終決策權。runtime state 固定存放在 `%LOCALAPPDATA%\AgentCommander`，不會寫入工作專案；既有 `.agentcommander/` portable handoff 仍受支援。

## END USER

1. 下載並解壓縮 `AgentCommander-Portable.zip`。
2. 執行 `AgentCommander.exe`，選擇 `AUTO`（建議）或其他模式。
3. 在 **Worker Models** 從 Pi 已發現的模型建立別名，例如 `qwen-main`，並設為 Default。
4. 按 **Install Codex Integration**，狀態顯示 READY 後即可關閉 GUI。
5. 在 Orca 開啟任何 Git project，照常使用 Codex；不需手動啟動 Pi、輸入 `delegate_pi`，也不需每次提醒使用 AgentCommander。

GUI 只是設定控制程式，不是 daemon。關閉後 Codex 仍會按需啟動 `AgentCommanderMCP.exe mcp`。Portable 版內含 Python runtime；使用者電腦仍須有 Codex CLI、Pi、已配置的 Local Qwen，以及專案流程所需的 Git。

### Modes

- **OFF**：禁止 Pi delegation；`delegate_pi` 會以 `AGENT_COMMANDER_DISABLED` 硬性失敗。
- **AUTO**：預設模式。Codex 對 substantial implementation、debugging、refactoring、repo investigation 與 tests 自行判斷是否委派。
- **FORCE**：coding implementation 強烈優先委派；純問答、工具不可用或使用者明確要求不用時除外。

使用者當次指示（例如「這次不要用 AgentCommander」）只覆蓋目前工作，不修改全域模式。

Worker lifetime 依工作範圍選擇：小型或 repair 為 1800 秒、一般 substantial implementation 為 3600 秒、明顯大型 milestone 或大型 repository refactor 為 5400 秒；仍受 14,400 秒安全上限約束。`wait_for_task` timeout 只控制 polling，不會改變 worker lifetime。

Windows worker 由 AgentCommander 以 owned process tree 管理。Timeout 會先進入 `TERMINATING`，以 Job Object／PID tree 終止並確認所有已記錄 descendants 消失後才成為 `TIMED_OUT`；若無法確認則為 `TERMINATION_FAILED` 或 `ORPHAN_WORKER`，全域 single-worker slot 會保持鎖定，禁止下一個 delegation。

每個 task result 會分開記錄 Worker process、Worker claim、測試、path validation、final acceptance、Worker token/runtime 與成本觀測。Qwen 的 input/output/cache-read token 只能作為本機 Worker 資源指標；Codex 規劃、等待、MCP tool calls、review、repair 用量只有平台提供時才填入，否則明確標示不可取得，不會估算 Codex 額度或金額。要比較 Codex 直接完成與 Codex＋Qwen，必須使用範圍和品質相近的多筆 task result；單次 Worker token 或測試通過不足以宣稱節省。

設定檔位於 `%LOCALAPPDATA%\AgentCommander\config.json`。模型 profile 只引用 Pi 已配置的 model identifier；AgentCommander 不下載模型、不修改 Pi provider 設定，也不會在 profile 遺失時偷偷 fallback。

### A/B benchmark

Run `python scripts/prepare_benchmark.py` to create two identical disposable Git projects: `direct` and `agentcommander`. Run the same `BENCHMARK_PROMPT.md` in both: do the first without delegation and the second with AgentCommander AUTO. Fill each `BENCHMARK_RESULT.json` with the Codex client usage, elapsed time, repairs, tests, and quality notes; for the delegated run also copy `cost_metrics.worker` from `get_task_result`. Compare quality and tests first, then only the metrics actually available. Never treat Qwen tokens as Codex quota.

## DEVELOPER

```powershell
git clone https://github.com/zillawillysu-a11y/AgentCommander.git
cd AgentCommander
python -m pip install -r requirements.txt
python -m pytest -q
python -m commander
```

Source mode 保留 `python -m commander` STDIO MCP 與既有 bootstrap/doctor 流程。建立 Windows Portable：

```powershell
.\build.ps1
```

輸出：

```text
dist\AgentCommander\AgentCommander.exe
dist\AgentCommander\AgentCommanderMCP.exe
dist\AgentCommander\_internal\...
dist\AgentCommander-Portable.zip
```

`AgentCommanderMCP.exe` 支援 `mcp`、`worker <task-id>`、`doctor --json`。Codex integration 只管理名為 `agent-commander` 的 MCP 與全域 `AGENTS.md` 中界定清楚的 managed block；移除時不碰其他 MCP 或使用者規則。

機器上的實際 model ID、credentials、auth、runtime state、worker logs 與建置輸出不得提交到公開 repository。
