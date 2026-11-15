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
