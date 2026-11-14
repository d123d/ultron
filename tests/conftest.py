import subprocess
import sys
from pathlib import Path

import pytest
import yaml

FAKE = str(Path(__file__).parent / "fixtures" / "fake_worker.py")
BD = Path(r"C:\AI\tools\beads\bd.exe")


def fake(mode: str, **extra) -> dict:
    return {"kind": "agent", "control": "command", "cost": extra.pop("cost", "free"),
            "capabilities": extra.pop("capabilities", ["code"]), "argv": [sys.executable, FAKE, mode, "{prompt_file}", "{cwd}"],
            **extra}


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Isolated state dir + registry with fake workers; returns (registry_path, state_dir)."""
    state = tmp_path / "state"
    reg = {
        "defaults": {"timeout_s": 60, "quota_patterns": ["usage limit", "429"], "cooldown_s": 600},
        "workers": {
            "fake-ok": fake("ok", rank=10),
            "fake-liar": fake("liar"),
            "fake-quota": fake("quota", rank=1),
            "fake-error": fake("error", rank=2),
            "fake-sleep": fake("sleep"),
            "fake-echo": fake("echo"),
            "fake-half": fake("half"),
            "fake-silent": fake("silent"),
            "fake-cheat": fake("cheat"),
            "fake-gpu": fake("ok", resources=["gpu"], rank=5),
            "fake-off": fake("ok", enabled=False),
            "fake-claude": fake("ok", opt_in=True, cost="claude"),
        },
        "tiers": {},
        "routing": {"cost_order": ["free", "flat", "metered", "claude"], "chains": {}},
        "board": {"bd_path": str(BD), "dir": str(tmp_path / "board"), "prefix": "tb"},
    }
    path = tmp_path / "workers.yaml"
    path.write_text(yaml.safe_dump(reg), encoding="utf-8")
    monkeypatch.setenv("FLEET_REGISTRY", str(path))
    monkeypatch.setenv("FLEET_STATE_DIR", str(state))
    return path, state


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    for args in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        subprocess.run(["git", *args], cwd=r, check=True)
    (r / "README.md").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=r, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=r, check=True)
    return r


HELLO_CHECK = 'python -c "import pathlib,sys; sys.exit(0 if pathlib.Path(\'hello.txt\').read_text()==\'hello\' else 1)"'
