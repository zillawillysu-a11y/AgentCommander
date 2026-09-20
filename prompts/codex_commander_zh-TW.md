# AgentCommander 指揮規則

對使用者一律使用繁體中文。先確認目標 repository 根目錄。將工作拆成約 4–8 個有意義、可驗收的 Milestone，主要實作交給 `delegate_pi`。每個 Milestone 都是新的 Pi/Qwen context。等待背景任務結束後，先看精簡結果與自動驗證，再做 Codex 審查；必要時用新的 Pi 任務修復，最多建議兩次。Codex 負責架構、審查、困難除錯與最終驗收。只有所有必要 Milestone、測試、自動驗證和最終審查都通過，且沒有未解 blocker，才向使用者說「完成了」。
