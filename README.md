# AgentCommander V0.1

AgentCommander 是 Codex CLI 的本機 STDIO MCP server。Codex 擔任 Commander，透過 AgentCommander 把實作 Milestone 交給 Pi 與既有的 Local Qwen。

`Codex → AgentCommander → Pi → Local Qwen → 自動驗證 → Codex 審查`

每個 Milestone 啟動全新的 Pi process，使用 `--no-session`，避免舊對話造成 context 膨脹。Worker prompt 透過 `@檔案` 傳入，並從 JSONL 確認 Pi 收到完整任務。Worker 完成後，AgentCommander 以派工時的 Git commit 為基線，自動檢查允許修改的路徑與測試，只把精簡結果交給 Codex；完整 JSONL 留在本機 `state/`。

## 快速開始（Windows 11）

```powershell
git clone https://github.com/zillawillysu-a11y/AgentCommander.git
cd AgentCommander
.\bootstrap.ps1
.\doctor.ps1
```

`bootstrap.ps1` 會安裝本專案缺少的 Python 依賴，並在目前的 `CODEX_HOME` 與標準 Codex CLI 設定註冊 `agent-commander` MCP；不會更動 Pi 模型或登入設定。亦可單獨執行 `install_mcp.bat`、`uninstall_mcp.bat`。安裝後重新開啟 Codex CLI；Orca 可作為前端，但不是必要條件。

向 Codex 說：

> 使用 AgentCommander 完成這個專案。你是 Commander。請把工作拆成合理的 Milestone。主要實作交給 Pi/Qwen。每個 Milestone 使用 Fresh Worker Context。你負責驗收、必要時要求修正。全部完成並通過驗收後再告訴我。

## 設定與結果

預設設定見 `config.example.json`；可建立不進 Git 的 `config.local.json` 覆寫。`delegate_pi` 接受目標 Git repository、目標、驗收條件、允許路徑、驗證命令及逾時秒數。命令以 argv 陣列傳入，例如 `["python", "-m", "pytest", "-q"]`。V0.1 同時只允許一個 Worker。任務結果在 `state/tasks/TASK-xxxxxx/`；Codex MCP 回傳精簡摘要。Worker 自稱 PASS 不代表完成，必須通過自動驗證與 Codex 最終審查。

## 開發驗證

```powershell
python -m pytest -q
git diff --check
```
