# AgentCommander 0.2.0

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

設定檔位於 `%LOCALAPPDATA%\AgentCommander\config.json`。模型 profile 只引用 Pi 已配置的 model identifier；AgentCommander 不下載模型、不修改 Pi provider 設定，也不會在 profile 遺失時偷偷 fallback。

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
