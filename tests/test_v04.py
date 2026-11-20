"""Ultron v0.4.0: adapters, providers, bake-off learning, embeddings, costs,
streaming, sync --add, weights merge, privacy gate."""

import io
import json
import sys
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from ai_neural_network import costs, providers, registry
from ai_neural_network.network import Network
from ai_neural_network.procs import FleetError
from ai_neural_network.registry import Registry, Tier

ROOT = Path(__file__).parent.parent
CONFIG = ROOT / "config"


def tier(**kw):
    base = {"id": "x", "base_url": "https://example.com/v1", "model": "m", "cost": "metered"}
    base.update(kw)
    return Tier(**base)


def secrets(mapping):
    def req(name):
        if name in mapping:
            return mapping[name]
        raise FleetError(f"secret {name} not set")

    return req, lambda name: mapping.get(name, "")


BODY = {
    "model": "m",
    "messages": [{"role": "system", "content": "be terse"}, {"role": "user", "content": "hi"}],
    "temperature": 0.2,
    "max_tokens": 50,
}


# --- 1. adapters ---
def test_azure_build():
    req, opt = secrets({"AZURE_OPENAI_API_KEY": "k1", "AZURE_OPENAI_ENDPOINT": "https://res.openai.azure.com/"})
    url, headers, payload, mode = providers.build(
        tier(adapter="azure", key_env="AZURE_OPENAI_API_KEY", model="gpt-5.5"), BODY, req, opt
    )
    assert url == "https://res.openai.azure.com/openai/deployments/gpt-5.5/chat/completions?api-version=2024-10-21"
    assert headers["api-key"] == "k1" and "model" not in payload


def test_bedrock_build_parse_and_sigv4():
    req, opt = secrets({"AWS_ACCESS_KEY_ID": "AKID", "AWS_SECRET_ACCESS_KEY": "sek", "AWS_REGION": "eu-west-1"})
    url, headers, payload, _ = providers.build(
        tier(adapter="bedrock", key_env="AWS_ACCESS_KEY_ID", model="openai.gpt-oss-120b"), BODY, req, opt
    )
    assert url.startswith("https://bedrock-runtime.eu-west-1.amazonaws.com/model/openai.gpt-oss-120b/converse")
    assert payload["system"] == [{"text": "be terse"}]
    assert payload["messages"] == [{"role": "user", "content": [{"text": "hi"}]}]
    assert headers["Authorization"].startswith("AWS4-HMAC-SHA256 Credential=AKID/")
    out = providers.parse(
        "bedrock",
        {
            "output": {"message": {"content": [{"text": "hello"}]}},
            "usage": {"inputTokens": 3, "outputTokens": 1},
            "stopReason": "end_turn",
        },
    )
    assert out["choices"][0]["message"]["content"] == "hello" and out["usage"]["prompt_tokens"] == 3
    h1 = providers.sigv4_headers(
        method="POST",
        host="h",
        path="/p",
        body=b"{}",
        access="A",
        secret_key="S",
        region="us-east-1",
        service="bedrock",
        when=datetime(2026, 1, 1, tzinfo=UTC),
    )
    h2 = providers.sigv4_headers(
        method="POST",
        host="h",
        path="/p",
        body=b"{}",
        access="A",
        secret_key="S",
        region="us-east-1",
        service="bedrock",
        when=datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert h1 == h2  # deterministic signing


def test_vertex_express_build_parse(monkeypatch):
    monkeypatch.setattr(providers.shutil, "which", lambda _: None)
    req, opt = secrets({"VERTEX_API_KEY": "vk"})
    url, headers, payload, mode = providers.build(
        tier(adapter="vertex", key_env="VERTEX_API_KEY", model="gemini-3.5-flash"), BODY, req, opt
    )
    assert url.endswith("/publishers/google/models/gemini-3.5-flash:generateContent") and mode == "vertex-gemini"
    assert headers["x-goog-api-key"] == "vk"
    assert payload["contents"] == [{"role": "user", "parts": [{"text": "hi"}]}]
    out = providers.parse(
        "vertex",
        {
            "candidates": [{"content": {"parts": [{"text": "yo"}]}}],
            "usageMetadata": {"promptTokenCount": 2, "candidatesTokenCount": 1},
        },
        mode,
    )
    assert out["choices"][0]["message"]["content"] == "yo"


def test_cloudflare_build_and_missing_account():
    req, opt = secrets({"CLOUDFLARE_API_TOKEN": "ct", "CLOUDFLARE_ACCOUNT_ID": "acc9"})
    t = tier(
        adapter="cloudflare",
        key_env="CLOUDFLARE_API_TOKEN",
        base_url="https://api.cloudflare.com/client/v4/accounts/{account}/ai/v1",
    )
    url, headers, _, _ = providers.build(t, BODY, req, opt)
    assert "/accounts/acc9/ai/v1/chat/completions" in url and headers["Authorization"] == "Bearer ct"
    req2, opt2 = secrets({"CLOUDFLARE_API_TOKEN": "ct"})
    with pytest.raises(FleetError):
        providers.build(t, BODY, req2, opt2)


def test_adapter_tiers_registered():
    reg = registry.load()
    assert {t.id for t in reg.tiers.values() if t.adapter} == {"azure-api", "bedrock-api", "vertex-api", "cloudflare-api"}


# --- 2. providers command data ---
def test_provider_rows_key_presence(monkeypatch):
    from ai_neural_network import doctor, llm

    monkeypatch.setattr(llm, "_secret_or_none", lambda name: "x" if name == "OPENAI_API_KEY" else None)
    rows = {r["id"]: r for r in doctor.provider_rows(registry.load())}
    assert rows["openai-api"]["key_present"] is True
    assert rows["azure-api"]["key_present"] is False
    assert rows["strata"]["key_present"] is None  # keyless local tier


# --- 3. bake-off scores feed synapses ---
def test_apply_scores_moves_edges():
    from ai_neural_network.evals import apply_scores

    net = Network.from_config(CONFIG)
    edge = next(e for e, w in net.synapses.items() if e[1] == "main" and w > 0)
    before = net.synapses[edge]
    changed = apply_scores(net, [{"tier": "main", "score": 10, "n": 10}])
    assert changed and net.synapses[edge] > before
    apply_scores(net, [{"tier": "main", "score": 0, "n": 10}])
    assert net.synapses[edge] == pytest.approx(before)


# --- 4. embeddings memory ---
def test_remember_recall(monkeypatch, tmp_path):
    from ai_neural_network import memory_embed

    monkeypatch.setenv("FLEET_STATE_DIR", str(tmp_path))
    vecs = {"cats are small": [1.0, 0.0], "dogs are loyal": [0.0, 1.0], "cats": [0.9, 0.1]}
    monkeypatch.setattr(memory_embed, "embed", lambda reg, text, spec="openai-embed": vecs[text])
    reg = registry.load()
    memory_embed.remember(reg, "cats are small")
    memory_embed.remember(reg, "dogs are loyal")
    hits = memory_embed.recall(reg, "cats")
    assert hits[0]["text"] == "cats are small" and hits[0]["score"] > hits[1]["score"]


# --- 5. cost neuromodulation ---
def test_cost_modulation():
    reg = registry.load()
    today = datetime.now(UTC).date().isoformat()
    rows = [{"type": "ask", "tier": "openai-api", "ts": today, "input_tokens": 1_000_000, "output_tokens": 0}]
    assert costs.spend_today(reg, rows) == {"openai-api": 2.0}
    assert costs.modulation(reg, 5.0, rows) == pytest.approx(1.0 - 0.8 * 0.4)
    big = [{"type": "ask", "tier": "openai-api", "ts": today, "input_tokens": 100_000_000, "output_tokens": 0}]
    assert costs.modulation(reg, 5.0, big) == 0.2  # floor
    assert costs.modulation(reg, 5.0, []) == 1.0


# --- 6. streaming ---
def test_ask_stream(monkeypatch, tmp_path):
    from ai_neural_network import llm

    monkeypatch.setenv("FLEET_STATE_DIR", str(tmp_path))
    reg = Registry(
        workers={},
        tiers={"s": Tier(id="s", base_url="https://x.example/v1", model="m", cost="free")},
        cost_order=["free"],
        chains={},
        board={},
    )
    sse = b'data: {"choices":[{"delta":{"content":"he"}}]}\n\ndata: {"choices":[{"delta":{"content":"llo"}}]}\n\ndata: [DONE]\n'

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(llm.urllib.request, "urlopen", lambda req, timeout=0: Resp(sse))

    @contextmanager
    def no_slot(*a, **k):
        yield

    monkeypatch.setattr(llm, "slot", no_slot)
    monkeypatch.setattr(llm, "_not_in_cooldown", lambda ids: ids)
    assert "".join(llm.ask_stream(reg, "s", "hi")) == "hello"
    with pytest.raises(FleetError):  # adapter tiers cannot stream
        reg.tiers["s"] = Tier(id="s", base_url="azure://openai", model="m", cost="metered", adapter="azure")
        "".join(llm.ask_stream(reg, "s", "hi"))


# --- 7. sync --add scaffold ---
def test_scaffold_add_neuron(tmp_path):
    import shutil

    from ai_neural_network.scaffold import add_neuron

    cfg = tmp_path / "config"
    shutil.copytree(CONFIG, cfg)
    add_neuron("new-provider", region="association", capabilities=["text"], config_dir=cfg)
    net = Network.from_config(cfg)
    assert "new-provider" in net.neurons and ("main", "new-provider") in net.synapses
    with pytest.raises(FleetError):
        add_neuron("new-provider", config_dir=cfg)


# --- 8. weights merge ---
def test_merge_weights(tmp_path):
    import shutil

    cfg = tmp_path / "config"
    shutil.copytree(CONFIG, cfg)
    net = Network.from_config(cfg)
    edge = next(iter(net.synapses))
    key = f"{edge[0]}->{edge[1]}"
    out = net.merge_weights({key: 1.0, "main->ghost-neuron": 0.3, "main->gemini-api": 0.9})
    assert out["merged"] == [key] and net.synapses[edge] == pytest.approx((Network.from_config(CONFIG).synapses[edge] + 1.0) / 2)
    assert out["added"] == ["main->gemini-api"]  # adopted: both neurons exist; ghost-neuron edge dropped
    assert (cfg / "weights.json").exists()


# --- 9. privacy gate ---
def test_private_fire_selects_local_only():
    net = Network.from_config(CONFIG)
    out = net.fire("write code", capability="code", private=True, dry_run=True, reg=registry.load())
    assert out["chosen"] in {"strata", "tiny", "local"}
    with pytest.raises(FleetError):
        net.fire("write code", capability="code", private=True, dry_run=True, worker="codex", reg=registry.load())


def test_private_worker_neuron_refused():
    net = Network.from_config(CONFIG)
    out = net.neurons["openhuman"].fire(0, prompt="x", reg=registry.load(), private=True)
    assert out["ok"] is False and "private" in out["error"]


def test_counts_v04():
    net = Network.from_config(CONFIG)
    reg = registry.load()
    assert len(net.neurons) >= 61 and len(reg.tiers) >= 55 and net.sync_missing() == []
    assert json.loads((CONFIG / "weights.json").read_text())  # weights file parses
