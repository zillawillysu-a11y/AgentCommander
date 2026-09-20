@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "ACTIVE_CODEX_HOME=%CODEX_HOME%"
call :ensure
if errorlevel 1 exit /b 1
if defined ACTIVE_CODEX_HOME (
  set "CODEX_HOME="
  call :ensure
  if errorlevel 1 exit /b 1
)
echo agent-commander MCP 安裝完成。
exit /b 0
:ensure
call codex mcp get agent-commander >nul 2>nul
if not errorlevel 1 exit /b 0
call codex mcp add agent-commander --env "PYTHONPATH=%~dp0." -- python -m commander
exit /b %errorlevel%
