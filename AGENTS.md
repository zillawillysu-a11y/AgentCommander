# AgentCommander 專案規則

- Canonical repository root is the current AgentCommander repository. Never drift to sibling repositories.
- External `project_root` passed into `delegate_pi` is a target repository, not AgentCommander source.
- Only inspect target repositories when executing explicitly requested integration tasks.
- Use targeted file reads and avoid large logs.
- Never use destructive Git commands or rewrite Git history.
- Respect public repository secret rules: never commit credentials, local config, worker logs, task state, or temporary integration repositories.
- Human-facing output uses Traditional Chinese (zh-TW). Pi worker prompts default to English.
