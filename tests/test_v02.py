import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from commander import config, entrypoint, integration, models, server


@pytest.fixture
def machine_config(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local App Data 測試"))
    data = copy.deepcopy(config.DEFAULT)
    data["models"] = {"qwen-main": {"pi_model": "llama-cpp/C:/Models/Qwen.gguf"}}
    data["worker"]["default_profile"] = "qwen-main"
    config.save_config(data)
    return data


@pytest.mark.parametrize("mode", ["OFF", "AUTO", "FORCE"])
def test_mode_persistence(mode, machine_config):
    config.update_mode(mode)
    assert config.load_config()["mode"] == mode
    assert config.config_path().parent.name == "AgentCommander"


def test_invalid_mode(machine_config):
    with pytest.raises(ValueError, match="OFF, AUTO, or FORCE"):
        config.update_mode("sometimes")


def test_profiles_and_model_syntax(machine_config):
    discovered = ["llama-cpp/C:/Models/Qwen.gguf"]
    assert models.resolve_profile(config=machine_config, discovered=discovered) == ("qwen-main", discovered[0])
    assert models.pi_model_args(discovered[0]) == ["--model", discovered[0]]
    with pytest.raises(ValueError, match="NOT_FOUND"): models.resolve_profile("missing", machine_config, discovered)
    with pytest.raises(ValueError, match="UNAVAILABLE"): models.resolve_profile(config=machine_config, discovered=[])
    assert models.list_profiles(machine_config, discovered)[0]["is_default"]


def test_off_hard_block_before_launch(tmp_path, monkeypatch, machine_config):
    root = tmp_path / "repo"; root.mkdir(); subprocess.run(["git", "init", "-q", str(root)], check=True)
    config.update_mode("OFF"); launched = []
    monkeypatch.setattr(server, "launch", lambda task_id: launched.append(task_id))
    with pytest.raises(ValueError, match="AGENT_COMMANDER_DISABLED"):
        server.delegate_pi(str(root), "work", ["done"], ["src/**"], [[sys.executable, "-c", "pass"]])
    assert launched == []


def test_managed_block_lifecycle_and_preservation(tmp_path):
    path = tmp_path / "Codex Home 空格" / "AGENTS.md"; original = "before\r\ncustom rules\r\nafter"
    path.parent.mkdir(); path.write_text(original, encoding="utf-8")
    integration.install_managed_block(path); first = path.read_text(encoding="utf-8")
    assert integration.BEGIN in first and "custom rules" in first
    integration.install_managed_block(path); assert path.read_text(encoding="utf-8").count(integration.BEGIN) == 1
    assert integration.remove_managed_block(path); result = path.read_text(encoding="utf-8")
    assert "custom rules" in result and integration.BEGIN not in result


def test_command_generation_windows_spaces(monkeypatch):
    helper = Path("C:/Program Files/Agent Commander/AgentCommanderMCP.exe")
    command = integration.mcp_add_command(helper)
    assert command[-2:] == [str(helper.resolve()), "mcp"]
    monkeypatch.setattr(entrypoint, "packaged", lambda: False)
    assert entrypoint.worker_command("TASK-000001") == [sys.executable, "-m", "commander.pi_worker", "TASK-000001"]
    monkeypatch.setattr(entrypoint, "packaged", lambda: True); monkeypatch.setattr(entrypoint, "helper_path", lambda: helper)
    assert entrypoint.worker_command("TASK-000001") == [str(helper), "worker", "TASK-000001"]


def test_mcp_discovery_includes_v02_tools():
    tools = {tool.name for tool in __import__("asyncio").run(server.mcp.list_tools())}
    assert {"get_agentcommander_status", "list_worker_models", "set_mode", "delegate_pi"} <= tools
