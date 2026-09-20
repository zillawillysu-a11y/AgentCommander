# AgentCommander

AgentCommander 是 Codex CLI 的本機 STDIO MCP server。Codex 擔任 Commander，透過 AgentCommander 將實作 Milestone 交給 Pi 與既有的 Local Qwen。

`Codex → AgentCommander → Pi → Local Qwen → 自動驗證 → Codex 審查`

每個 Milestone 都啟動全新的 Pi process，使用 `--no-session`。Worker prompt 透過 `@檔案` 傳入，並從 JSONL 確認 Pi 收到完整任務。Worker 完成後，AgentCommander 以派工時的 Git commit 為基線，檢查允許路徑與測試；Codex 只收到精簡結果。

## 快速開始（Windows 11）

```powershell
git clone https://github.com/zillawillysu-a11y/AgentCommander.git
cd AgentCommander
.\bootstrap.ps1
.\doctor.ps1
```

`bootstrap.ps1` 安裝本專案缺少的 Python 依賴，並在目前的 `CODEX_HOME` 與標準 Codex CLI 設定註冊 `agent-commander` MCP；不會更動 Pi 模型或登入設定。亦可單獨執行 `install_mcp.bat`、`uninstall_mcp.bat`。安裝後重新開啟 Codex CLI。Orca 可作為前端，但不是必要條件。

向 Codex 說：

> 使用 AgentCommander 完成這個專案。你是 Commander。請把工作拆成合理的 Milestone。主要實作交給 Pi/Qwen。每個 Milestone 使用 Fresh Worker Context。你負責驗收、必要時要求修正。全部完成並通過驗收後再告訴我。

## Runtime state

預設位於 `%LOCALAPPDATA%\AgentCommander\`，不存入 AgentCommander 或目標專案的 Git repository：

```text
%LOCALAPPDATA%\AgentCommander\
  state\
    counter.txt
    task_index\
    repos\
      <repo-id>\
        repo.json
        tasks\TASK-000001\...
```

`repo-id` 是目標 repository 正規化絕對路徑的 SHA256 前 16 個十六進位字元。Task ID 全域遞增，因此原有的 `get_task_status(task_id)` 等 MCP 呼叫不需增加參數。`repo.json` 保存 `project_root`、`repo_id`、`created_at`、`last_used_at`。完整 JSONL、stderr、PID、驗證 logs 與 Worker token metrics 都留在本機 runtime 目錄。可在不進 Git 的 `config.local.json` 設定 `runtime.state_root` 改變位置；預設值見 `config.example.json`。

舊版的 `AgentCommander/state/tasks` **不會自動搬移或刪除**。AgentCommander 仍可唯讀查詢舊任務，新的 Task ID 會避開舊編號。

## 可選的專案交接

需要跨電腦繼續時，明確呼叫 MCP 工具 `export_project_handoff(project_root, decisions)`，在目標 repository 建立：

```text
.agentcommander/
  STATUS.md
  MILESTONES.json
  DECISIONS.md
```

這三個檔案只含精簡狀態、Milestone 結果與重要決策，可自行審查後納入目標專案 Git。它們不包含 Worker 對話、JSONL、logs、PID 或 token 原始資料。`get_project_handoff` 可檢視內容。沒有 `.agentcommander` 時，派工照常運作；存在時，`delegate_pi` 會把有長度上限的交接摘要交給新的 Worker。換電腦後只需 clone/pull 目標專案，不需舊 Pi session 或舊 runtime state。

## 設定與驗證

`delegate_pi` 接受目標 Git repository、目標、驗收條件、允許路徑、驗證命令及逾時秒數。命令以 argv 陣列傳入，例如 `["python", "-m", "pytest", "-q"]`。一次只執行一個 Worker。Worker 自稱 PASS 不代表完成，必須通過自動驗證與 Codex 最終審查。

```powershell
python -m pytest -q
git diff --check
```
