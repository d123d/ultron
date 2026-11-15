"""Provider slots, job slots, ad-hoc provider:model tiers."""
import pytest

from ai_neural_network import registry, slots
from ai_neural_network.llm import AskResult, candidates
from ai_neural_network.procs import FleetError


def test_one_slot_per_provider_with_models():
    reg = registry.load()
    rows = slots.provider_slots(reg)
    names = [r["provider"] for r in rows]
    assert len(names) == len(set(names))  # exactly one slot per provider
    by = {r["provider"]: r for r in rows}
    assert {"nvidia", "openrouter", "strata", "claude", "codex", "agy", "copilot"} <= set(by)
    assert "nim-ultra" in by["nvidia"]["tiers"] and "nvidia/nemotron-3-ultra-550b-a55b" in by["nvidia"]["models"]
    assert sum(len(r["tiers"]) for r in rows) == len(reg.tiers)  # every tier sits in exactly one slot


def test_adhoc_provider_model_tier():
    reg = registry.load()
    assert candidates(reg, "nvidia:some/new-model,") == ["nvidia:some/new-model"]
    t = reg.tiers["nvidia:some/new-model"]
    assert t.model == "some/new-model" and t.key_env == "NVIDIA_API_KEY" and not t.fallback
    with pytest.raises(FleetError):
        candidates(reg, "nosuchprovider:model,")


def test_shipped_jobs_reference_real_tiers_and_capabilities():
    assert slots.check_jobs(registry.load()) == []


def test_job_falls_through_to_next_model(monkeypatch):
    calls = []

    def fake_ask(reg, spec, prompt, **kw):
        calls.append(spec)
        if len(calls) == 1:
            raise FleetError("first model down")
        return AskResult(text="done", tier=spec.rstrip(","), model="m")

    monkeypatch.setattr(slots, "ask", fake_ask)
    monkeypatch.setattr(slots.ledger, "append", lambda row: None)
    out = slots.run_job(registry.load(), "quick", "hi")
    first, second = slots.load_jobs()["quick"]["tiers"][:2]
    assert calls == [first + ",", second + ","]
    assert out["status"] == "ok" and out["by"] == second and len(out["skipped"]) == 1
    with pytest.raises(FleetError):
        slots.run_job(registry.load(), "nosuchjob", "hi")


def test_every_harness_has_a_slot():
    reg = registry.load()
    rows = slots.harness_slots(reg)
    assert [r["harness"] for r in rows] == list(reg.workers)
    assert {"opencode", "codex", "agy", "pi", "claude", "github", "muse-code", "openhuman"} <= {r["harness"] for r in rows}


def test_learning_never_writes_into_the_repo_config(env):
    from ai_neural_network.network import Network
    from ai_neural_network.paths import config_dir

    net = Network.from_config()
    assert net.learned_path().parent == env[1]  # state dir, not config/
    before = (config_dir() / "weights.json").read_text(encoding="utf-8")
    net.sleep()
    assert (config_dir() / "weights.json").read_text(encoding="utf-8") == before and net.learned_path().exists()


def test_redact_covers_the_secrets_file(tmp_path, monkeypatch):
    from ai_neural_network import procs

    (tmp_path / ".secrets").mkdir()
    (tmp_path / ".secrets" / ".env").write_text("SOME_API_KEY=abcd1234efgh5678\nAWS_REGION=us-east-1-region\n", encoding="utf-8")
    monkeypatch.setattr(procs.Path, "home", lambda: tmp_path)
    assert procs.redact("error: key abcd1234efgh5678 rejected") == "error: key [REDACTED] rejected"
    assert procs.redact("us-east-1-region") == "us-east-1-region"


def test_worker_prompt_lists_matching_skills(tmp_path, monkeypatch):
    from ai_neural_network.card import TaskCard, render_prompt

    (tmp_path / "blender").mkdir()
    (tmp_path / "blender" / "SKILL.md").write_text("x", encoding="utf-8")
    monkeypatch.setenv("ULTRON_SKILLS_DIR", str(tmp_path))
    prompt = render_prompt(TaskCard(goal="Render the Blender kit"), None)
    assert str(tmp_path / "blender" / "SKILL.md") in prompt  # installed + topic matches
    assert "world-builder" not in prompt  # topic matches but not installed here: skipped
    assert "SKILLS" not in render_prompt(TaskCard(goal="say hello"), None)
