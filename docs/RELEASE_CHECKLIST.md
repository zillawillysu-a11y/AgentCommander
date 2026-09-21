# Release checklist

1. Confirm the worktree is clean and `commander.__version__` matches the intended tag.
2. Run `build.ps1` on Windows. It must pass the source suite and packaged-helper integration tests.
3. Run `dist\AgentCommander\AgentCommanderMCP.exe doctor --json` and require `status: PASS`.
4. Verify `AgentCommander-Portable.zip` against `AgentCommander-Portable.zip.sha256`.
5. Create an annotated `vX.Y.Z` tag and push it. The GitHub workflow rebuilds, retests, and publishes both artifacts.
6. Smoke-test install, update, uninstall, one delegated task, durable notification lookup, and Orca wake-up.
7. Never upload config, credentials, models, runtime state, Worker logs, or task records.
