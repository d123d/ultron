import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
import yaml

ROOT = Path(__file__).parent.parent

def test_skills_installed_and_registered():
    cfg = yaml.safe_load((ROOT / "config" / "skills.yaml").read_text())["skills"]
    assert set(cfg) == {"ponytail", "caveman", "find-skills", "openrouter-typescript-sdk", "mmx-cli", "cloud-providers"}
    for name in cfg:
        text = (ROOT / "skills" / name / "SKILL.md").read_text()
        assert f"name: {name}" in text, name
