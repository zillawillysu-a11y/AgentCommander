import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT = {"pi": {"command": "pi", "default_timeout_seconds": 2700, "max_repairs": 2}, "worker": {"language": "en", "fresh_session": True}, "output": {"max_return_lines": 150}, "runtime": {"state_root": None}}


def load_config(root=ROOT):
    config = json.loads(json.dumps(DEFAULT))
    path = Path(root) / "config.local.json"
    if path.exists():
        local = json.loads(path.read_text(encoding="utf-8"))
        for section, values in local.items():
            if section not in config or not isinstance(values, dict):
                raise ValueError(f"Unknown config section: {section}")
            if set(values) - set(config[section]):
                raise ValueError(f"Unknown config keys: {set(values) - set(config[section])}")
            config[section].update(values)
    if not config["worker"]["fresh_session"] or config["worker"]["language"] != "en":
        raise ValueError("V0.1 requires fresh English Pi sessions")
    return config
