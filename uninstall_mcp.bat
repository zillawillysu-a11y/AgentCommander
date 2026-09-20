@echo off
chcp 65001 >nul
setlocal
call codex mcp get agent-commander >nul 2>nul
if errorlevel 1 (
  echo agent-commander 尚未註冊。
  exit /b 0
)
call codex mcp remove agent-commander
if errorlevel 1 exit /b 1
echo agent-commander MCP 已移除。
