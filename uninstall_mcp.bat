@echo off
chcp 65001 >nul
setlocal
set "ACTIVE_CODEX_HOME=%CODEX_HOME%"
call :remove
if errorlevel 1 exit /b 1
if defined ACTIVE_CODEX_HOME (
  set "CODEX_HOME="
  call :remove
  if errorlevel 1 exit /b 1
)
echo agent-commander MCP 已移除。
exit /b 0
:remove
call codex mcp get agent-commander >nul 2>nul
if errorlevel 1 exit /b 0
call codex mcp remove agent-commander
exit /b %errorlevel%
