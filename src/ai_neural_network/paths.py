import os
from pathlib import Path


def project_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "config" / "neurons.yaml").is_file():
            return parent
    return Path(r"C:\AI\projects\ultron")


def config_dir() -> Path:
    env = os.environ.get("BRAIN_CONFIG_DIR")
    return Path(env) if env else project_root() / "config"


def state_dir() -> Path:
    return Path(
        os.environ.get("BRAIN_STATE_DIR")
        or os.environ.get("FLEET_STATE_DIR")
        or r"C:\AI\state\ai-neural-network"
    )


def runs_dir() -> Path:
    return state_dir() / "runs"


def ledger_path() -> Path:
    return state_dir() / "ledger.jsonl"


def health_path() -> Path:
    return state_dir() / "health.json"


def throttle_path() -> Path:
    return state_dir() / "throttle.json"


def weights_path() -> Path:
    return config_dir() / "weights.json"


def registry_path() -> Path:
    return Path(
        os.environ.get("BRAIN_REGISTRY")
        or os.environ.get("FLEET_REGISTRY")
        or Path(__file__).with_name("workers.yaml")
    )
