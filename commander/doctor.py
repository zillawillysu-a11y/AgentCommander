import json
import shutil
from . import __version__
from .config import load_config, local_data_root
from .integration import integration_status
from .models import discover_pi_models, list_profiles

def run_doctor():
    config = load_config(); models = discover_pi_models(config); profiles = list_profiles(config, models); default = config["worker"].get("default_profile")
    checks = {"git": bool(shutil.which("git")), "codex": bool(shutil.which("codex")), "pi": bool(shutil.which(config["pi"]["command"])), "pi_models": bool(models), "selected_profile": any(x["profile"] == default and x["available"] for x in profiles), "runtime": True, "codex_integration": integration_status()}
    try: local_data_root().mkdir(parents=True, exist_ok=True)
    except OSError: checks["runtime"] = False
    return {"version": __version__, "status": "PASS" if all(checks.values()) else "WARN", "checks": checks, "models": models, "profiles": profiles}

def doctor_json(): return json.dumps(run_doctor(), ensure_ascii=False, indent=2)
