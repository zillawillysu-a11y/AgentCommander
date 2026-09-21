"""Deterministic watchdog for Pi's documented JSON event stream.

Pi 0.85 emits tool_execution_start(toolCallId, toolName, args) and
tool_execution_end(toolCallId, toolName, result, isError).  This module only
depends on those public event fields and tolerates unrelated/future events.
"""
from __future__ import annotations

import hashlib
import json
import re

READ_ONLY = {"read", "grep", "find", "ls"}
MUTATING = {"edit", "write"}


def _stable(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _normalize_text(value):
    if isinstance(value, dict):
        value = value.get("content", value.get("text", value))
    if isinstance(value, list):
        value = [item.get("text", item) if isinstance(item, dict) else item for item in value]
    text = _stable(value) if not isinstance(value, str) else value
    text = re.sub(r"\b(?:0x[0-9a-f]+|\d{4,})\b", "#", text.lower())
    return re.sub(r"\s+", " ", text).strip()[-4000:]


class LoopGuard:
    def __init__(self, settings, scope="NORMAL"):
        self.settings = settings
        self.budget = settings["tool_budgets"][scope]
        self.starts = {}
        self.calls = []
        self.completed = []
        self.warnings = []
        self.stop_reason = None
        self.actions_since_progress = 0
        self._repo_fingerprint = None

    def _warn(self, reason):
        if reason not in self.warnings:
            self.warnings.append(reason)

    def _stop(self, reason):
        self.stop_reason = self.stop_reason or reason
        return self.stop_reason

    def observe(self, event, repo_fingerprint=None):
        if self.stop_reason or not isinstance(event, dict):
            return self.stop_reason
        changed = repo_fingerprint is not None and self._repo_fingerprint is not None and repo_fingerprint != self._repo_fingerprint
        if repo_fingerprint is not None:
            self._repo_fingerprint = repo_fingerprint
        if changed:
            self.actions_since_progress = 0
        kind = event.get("type")
        if kind == "tool_execution_start":
            name = str(event.get("toolName", ""))
            fingerprint = f"{name}:{_stable(event.get('args', {}))}"
            self.starts[event.get("toolCallId")] = (name, fingerprint)
            self.calls.append(fingerprint)
            self.actions_since_progress += 1
            if len(self.calls) > self.budget:
                return self._stop("TOOL_BUDGET_REACHED")
            consecutive = 0
            for item in reversed(self.calls):
                if item != fingerprint: break
                consecutive += 1
            prior_failed = any(item.startswith(fingerprint + ":error:") for item in self.completed)
            if consecutive >= self.settings["repeat_stop"] and not prior_failed:
                return self._stop("DUPLICATE_TOOL_CALL")
            if consecutive >= self.settings["repeat_warn"]:
                self._warn("DUPLICATE_TOOL_CALL")
            for width in range(2, 5):
                needed = width * self.settings["cycle_stop_laps"]
                if len(self.calls) >= needed and len(set(self.calls[-width:])) > 1 and self.calls[-needed:] == self.calls[-width:] * self.settings["cycle_stop_laps"]:
                    return self._stop("CYCLIC_TOOL_PATTERN")
                warned = width * self.settings["cycle_warn_laps"]
                if len(self.calls) >= warned and len(set(self.calls[-width:])) > 1 and self.calls[-warned:] == self.calls[-width:] * self.settings["cycle_warn_laps"]:
                    self._warn("CYCLIC_TOOL_PATTERN")
            if self.actions_since_progress >= self.settings["no_progress_actions"]:
                return self._stop("NO_PROGRESS")
        elif kind == "tool_execution_end":
            name, call = self.starts.pop(event.get("toolCallId"), (str(event.get("toolName", "")), "unknown"))
            result = _normalize_text(event.get("result"))
            failed = bool(event.get("isError"))
            signature = f"{call}:{'error' if failed else 'ok'}:{hashlib.sha256(result.encode()).hexdigest()[:16]}"
            self.completed.append(signature)
            same = 0
            for item in reversed(self.completed):
                if item != signature: break
                same += 1
            if failed and same >= self.settings["repeat_stop"]:
                return self._stop("REPEATED_FAILURE")
            if failed and same >= self.settings["repeat_warn"]:
                self._warn("REPEATED_FAILURE")
            if not failed and name in READ_ONLY and same >= self.settings["repeat_stop"]:
                return self._stop("IDEMPOTENT_NO_PROGRESS")
            if not failed and name in READ_ONLY and same >= self.settings["repeat_warn"]:
                self._warn("IDEMPOTENT_NO_PROGRESS")
            if not failed and (name in MUTATING or changed):
                self.actions_since_progress = 0
        return self.stop_reason

    def diagnostics(self):
        return {"tool_actions": len(self.calls), "tool_budget": self.budget, "warnings": self.warnings, "stop_reason": self.stop_reason}
