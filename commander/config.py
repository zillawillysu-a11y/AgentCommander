"""Machine-local AgentCommander configuration."""
import copy
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODES = {"OFF", "AUTO", "FORCE"}
DEFAULT = {"mode": "AUTO", "pi": {"command": "pi", "default_timeout_seconds": 3600, "max_repairs": 2}, "worker": {"language": "en", "fresh_session": True, "default_profile": None, "max_output_tokens": None}, "guard": {"enabled": True, "repeat_warn": 2, "repeat_stop": 3, "cycle_warn_laps": 2, "cycle_stop_laps": 3, "no_progress_actions": 20, "tool_budgets": {"SMALL": 40, "NORMAL": 80, "LARGE": 140}}, "models": {}, "output": {"max_return_lines": 150}, "runtime": {"state_root": None}}

def local_data_root():
    value = os.environ.get("LOCALAPPDATA")
    if value: return Path(value) / "AgentCommander"
    if os.name == "nt": return Path.home() / "AppData" / "Local" / "AgentCommander"
    return Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "AgentCommander"

def config_path(): return local_data_root() / "config.json"

def _merge(config, values):
    for key, value in values.items():
        if key not in config: raise ValueError(f"Unknown config section: {key}")
        if isinstance(config[key], dict):
            if not isinstance(value, dict): raise ValueError(f"Invalid config section: {key}")
            if key != "models" and set(value) - set(config[key]): raise ValueError(f"Unknown config keys: {set(value) - set(config[key])}")
            config[key].update(value)
        else: config[key] = value

def validate_config(config):
    if config["mode"] not in MODES: raise ValueError("mode must be OFF, AUTO, or FORCE")
    if not config["worker"]["fresh_session"] or config["worker"]["language"] != "en": raise ValueError("AgentCommander requires fresh English Pi sessions")
    for name, profile in config["models"].items():
        if not name or not isinstance(profile, dict) or not isinstance(profile.get("pi_model"), str) or not profile["pi_model"]: raise ValueError(f"Invalid model profile: {name}")
    default = config["worker"].get("default_profile")
    if default is not None and not isinstance(default, str): raise ValueError("default_profile must be a string or null")
    output_limit = config["worker"].get("max_output_tokens")
    if output_limit is not None and (not isinstance(output_limit, int) or isinstance(output_limit, bool) or output_limit < 1): raise ValueError("max_output_tokens must be a positive integer or null")
    guard = config["guard"]
    positive = ("repeat_warn", "repeat_stop", "cycle_warn_laps", "cycle_stop_laps", "no_progress_actions")
    if any(not isinstance(guard.get(key), int) or isinstance(guard.get(key), bool) or guard[key] < 1 for key in positive): raise ValueError("guard thresholds must be positive integers")
    if guard["repeat_warn"] >= guard["repeat_stop"] or guard["cycle_warn_laps"] >= guard["cycle_stop_laps"]: raise ValueError("guard warning threshold must be below stop threshold")
    if set(guard["tool_budgets"]) != {"SMALL", "NORMAL", "LARGE"} or any(not isinstance(value, int) or isinstance(value, bool) or value < 1 for value in guard["tool_budgets"].values()): raise ValueError("guard tool_budgets must define positive SMALL, NORMAL, and LARGE limits")
    return config

def load_config(root=None):
    config = copy.deepcopy(DEFAULT)
    paths = [Path(root) / "config.local.json"] if root is not None else [ROOT / "config.local.json", config_path()]
    for path in paths:
        if path.exists(): _merge(config, json.loads(path.read_text(encoding="utf-8-sig")))
    return validate_config(config)

def save_config(config):
    validate_config(config); path = config_path(); path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp"); temp.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"); os.replace(temp, path)
    return path

def update_mode(mode):
    mode = str(mode).upper()
    if mode not in MODES: raise ValueError("mode must be OFF, AUTO, or FORCE")
    config = load_config(); config["mode"] = mode; save_config(config); return config
