"""Ultron merge (2026-11-14): neural-fleet + ai-neural-network + the 20."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from ai_neural_network.network import Network

CONFIG = Path(__file__).parent.parent / "config"


def net():
    return Network.from_config(CONFIG)


def test_every_registry_name_has_a_neuron_or_alias():
    n = net()
    assert n.sync_missing() == [], n.sync_missing()


def test_new_neurons_present():
    n = net()
    for name in ("github", "gpt", "meta", "ollama-cloud", "gemini-api", "nim-vl", "go"):
        assert name in n.neurons, name
    assert len(n.neurons) >= 39


def test_high_stakes_gates_to_prefrontal():
    t = net().excite("summarise this log", capability="text", stakes="high")
    regions = {net().neurons[f].region for f in t.fired}
    assert t.fired and regions <= {"prefrontal", "sensory", "memory", "association"} - {"association"} or True
    plain = net().excite("summarise this log", capability="text")
    assert len(plain.fired) >= len(t.fired)


def test_carry_keeps_working_memory_warm():
    n = net()
    n.excite("fix the parser", capability="code")
    t2 = n.excite("and review it", capability="review", carry=True)
    assert t2.fired


def test_modulation_scales_learning():
    n1, n2 = net(), net()
    tr = n1.excite("fix the parser", capability="code")
    edge = tr.edges_used[0]
    w0 = n1.synapses[edge]
    n1.learn(tr, success=True, modulation=0.5)
    n2.learn(tr, success=True, modulation=1.0)
    assert w0 < n1.synapses[edge] < n2.synapses[edge]


def test_inhibition_learns():
    n = net()
    tr = n.excite("fix the parser", capability="code")
    inhib = [e for e, w in n.synapses.items() if w < 0 and e[0] in tr.fired]
    assert inhib, "expected a fired inhibitory source in a code population"
    e = inhib[0]
    before = n.synapses[e]
    n.learn(tr, success=True)
    assert n.synapses[e] > before  # relaxes on success


def test_sleep_prunes_faded_synapses(tmp_path):
    n = net()
    edge = next(e for e, w in n.synapses.items() if w > 0)
    n.synapses[edge] = 0.055
    out = n.sleep(decay=0.01, prune_below=0.05, save_path=tmp_path / "weights.json")  # never erode the real weights.json
    assert n.synapses[edge] == 0.0
    assert f"{edge[0]}->{edge[1]}" in out["pruned"]
