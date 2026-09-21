"""Pi model discovery and profile resolution."""
import shutil
import subprocess
from .config import load_config
from .subprocess_utils import hidden_run_kwargs

def discover_pi_models(config=None):
    config = config or load_config(); command = shutil.which(config["pi"]["command"]) or config["pi"]["command"]
    try: result = subprocess.run([command, "--list-models"], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30, **hidden_run_kwargs())
    except (OSError, subprocess.TimeoutExpired): return []
    models = []
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) >= 2 and fields[0] != "provider" and not set(fields[0]) <= {"-"}: models.append(f"{fields[0]}/{fields[1]}")
    return models

def list_profiles(config=None, discovered=None):
    config = config or load_config(); available = set(discover_pi_models(config) if discovered is None else discovered); default = config["worker"].get("default_profile")
    return [{"profile": name, "pi_model": item["pi_model"], "available": item["pi_model"] in available, "is_default": name == default} for name, item in sorted(config["models"].items())]

def resolve_profile(name=None, config=None, discovered=None):
    config = config or load_config(); selected = name or config["worker"].get("default_profile")
    if not selected or selected not in config["models"]: raise ValueError(f"MODEL_PROFILE_NOT_FOUND: {selected or 'default'}")
    model = config["models"][selected]["pi_model"]; available = set(discover_pi_models(config) if discovered is None else discovered)
    if model not in available: raise ValueError(f"MODEL_PROFILE_UNAVAILABLE: {selected} ({model})")
    return selected, model

def pi_model_args(model_identifier): return ["--model", model_identifier]
