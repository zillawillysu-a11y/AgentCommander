# P2 repo map / context filter design

P2 is intentionally deferred until the P0/P1 runtime changes have more production evidence. It should remain an optional, local, bounded optimization rather than a new indexing subsystem.

Proposed interface:

- `build_repo_map(project_root, candidate_paths, token_budget=1000) -> RepoMap`
- `select_context(repo_map, objective, acceptance_criteria, allowed_paths) -> list[RepoSymbol]`
- task field `repo_context` containing a compact rendered map, with a configuration switch to disable it

The index should cache by repository HEAD plus dirty-path fingerprints under the external AgentCommander state root. Extraction should use language-light parsing for paths, classes, functions, signatures, imports, and nearby tests. Selection starts from explicit required/allowed paths and objective identifiers, follows at most one import/test relationship hop, ranks exact symbol/path matches first, and truncates deterministically to the token budget.

The Worker receives only the rendered selection, never the full index. Cache failures or unsupported languages silently fall back to the existing bounded Worker exploration. No embedding service, daemon, semantic database, or additional Worker is required. Future implementation acceptance should measure reduced exploratory tool calls without reducing task quality, and should include false-relevance and stale-cache tests.
