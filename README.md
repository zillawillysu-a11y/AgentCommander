# AgentCommander 0.2.0

AgentCommander 讓 Codex 擔任規劃、審查與最終決策者，並透過 MCP 將適合的 repository 工作交給本機 Pi / Qwen Worker。所有任務都受 Git baseline、允許路徑、明確驗收條件及確定性驗證約束；runtime state 預設保存在 `%LOCALAPPDATA%\AgentCommander`，不寫入目標 repository。

## 使用者安裝

1. 下載並解壓縮 `AgentCommander-Portable.zip`。
2. 執行 `AgentCommander.exe`，選擇 `AUTO`、`FORCE` 或 `OFF`。
3. 在 **Worker Models** 確認 Pi 已找到可用模型，並設定 Default profile。
4. 按下 **Install Codex Integration**，確認狀態為 READY。
5. 在 Orca 開啟既有 Git repository，再請 Codex 使用 AgentCommander。

AgentCommander 需要 Git 來建立 baseline 與驗證變更範圍。若資料夾尚未初始化，執行：

```powershell
git -C "C:\path\to\project" init
```

`git init` 只建立本機 repository，不會把檔案上傳到 GitHub。

GUI 是設定程式，不是常駐 daemon。關閉後 Codex 仍可按需啟動 `AgentCommanderMCP.exe mcp`。Portable 版包含 Python runtime，但電腦仍須有 Git、Codex CLI、Pi，以及已配置的本機模型。

## 模式

- **OFF**：禁止 Worker delegation；`delegate_pi` 回傳 `AGENT_COMMANDER_DISABLED`。
- **AUTO**：只在 substantial implementation、debugging、refactoring、repository investigation、tests 或重複性工作確實可能節省 Commander 成本時委派。
- **FORCE**：強烈偏好委派合適的 coding implementation；架構決策、微小修改與不適合 Worker 的工作仍由 Codex 處理。

使用者當下的明確指示永遠優先於模式設定。

## 有界委派協定

`delegate_pi` 支援 `IMPLEMENT`、`REFACTOR`、`TEST`、`DIAGNOSE`、`SEARCH` 與 `REVIEW`。每個任務都要提供 objective、acceptance criteria、allowed paths 與 verification commands。Worker contract 要求最小有效修改，禁止未授權的順手清理、架構重設、相依套件、公用 API 變更或範圍擴張。

Worker lifetime 預設為：

- SMALL 或 repair：1800 秒
- 一般 substantial work：3600 秒
- LARGE milestone 或大型 refactor：5400 秒
- 安全上限：14,400 秒

`wait_for_task` 的 timeout 只控制單次查詢，不會改變 Worker lifetime。

### 結果與驗證

`get_task_result` 回傳精簡結果：驗收狀態、摘要、變更檔案、測試計數、repair 次數、耗時、Worker usage 與停止原因。stdout、stderr、詳細檢查、路徑歸屬及 artifact paths 留在本機；只有 FAILED 或 PARTIAL 且需要調查時才呼叫 `get_task_diagnostics`。

最終驗收與測試執行分開判定。會修改檔案的任務若沒有 Worker 歸屬變更、缺少結構化 Worker claim，或模型輸出被截斷，即使既有 verification command 通過也不能標記為完成。

Runtime watchdog 會偵測重複呼叫、等價失敗、短週期循環、重複唯讀、長時間無進度及 scope 工具預算。Windows 上的 Worker、Git、驗證、repair 與終止程序均以無視窗方式啟動，並追蹤 owned process tree，避免殘留子程序。

### 完成報告與通知

`delegate_pi` 可指定 repository-relative `completion_report`；若未指定但根目錄已有 `BENCHMARK_RESULT.json`，會自動選用。Worker 完成後以 atomic write 合併驗證狀態、測試、耗時及 Worker metrics。

每次完成都會寫入耐久事件。連線仍存在時，MCP 會傳送 `agentcommander.task.completed` notice；漏接事件可由 `list_completion_notifications` 讀取並 acknowledge。

若任務從 Orca-managed Codex terminal 委派，AgentCommander 還會記住來源 terminal，完成後注入固定訊號以啟動後續 Codex 回合。無法唯一辨識 terminal 時不會猜測，仍保留耐久事件供其他 client 查詢。

## A/B benchmark 與 P2 gate

執行 `python scripts/prepare_benchmark.py` 會建立兩個相同的 disposable Git projects：`direct` 與 `agentcommander`。兩邊使用相同的 `BENCHMARK_PROMPT.md`，先比較品質與測試，再比較實際可取得的 Codex usage、elapsed time、repair 與 Worker metrics。Qwen token 不等於 Codex quota。

P2 repo map / context filter 目前刻意延後。只有在至少 10 個 NORMAL/LARGE 任務中，出現下列任一情況才開始實作：

- 至少 30% 任務因探索造成 loop-guard warning、工具預算壓力或錯讀大量無關檔案。
- 探索性讀取平均超過 15 次，且可證明影響完成時間或品質。
- 大型 repository 的重複實驗顯示 bounded repo map 能降低至少 20% 探索呼叫，且不降低成功率。

設計細節見 `docs/P2_REPO_MAP_DESIGN.md`。

## 開發

```powershell
git clone https://github.com/zillawillysu-a11y/AgentCommander.git
cd AgentCommander
python -m pip install -r requirements.txt
python -m pytest -q
python -m commander
```

建立並驗證 Windows Portable release：

```powershell
.\build.ps1
```

輸出：

```text
dist\AgentCommander\AgentCommander.exe
dist\AgentCommander\AgentCommanderMCP.exe
dist\AgentCommander\AgentCommanderBenchmark.exe
dist\AgentCommander-Portable.zip
dist\AgentCommander-Portable.zip.sha256
```

`build.ps1` 會先建立封裝版、執行完整測試，再以真正的 packaged helper 執行 process-tree 與 output-budget integration tests；任何一步失敗都不會產生可發布 ZIP。

`AgentCommanderMCP.exe` 支援 `mcp`、`worker <task-id>` 與 `doctor --json`。Codex integration 只管理名為 `agent-commander` 的 MCP、全域 `AGENTS.md` 中的 managed block，以及 managed Skill；移除時不修改其他 MCP 或使用者規則。

公開 repository 不得提交模型檔、credentials、auth、runtime state、Worker logs、task records 或本機設定。
