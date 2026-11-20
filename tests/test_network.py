import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from ai_neural_network import Network

CONFIG = Path(__file__).parent.parent / "config"


def net():
    return Network.from_config(CONFIG)


def test_code_stimulus_recruits_a_population_not_one_worker():
    t = net().excite("fix the parser", capability="code")
    assert len(t.fired) >= 2, t.fired
    assert any(n in t.fired for n in ("opencode", "pi")), t.fired  # motor output


def test_vision_stimulus_stays_in_receptive_fields():
    t = net().excite("what is in this image", capability="vision")
    assert "vision" in t.fired
    assert "opencode" not in t.fired  # no code capability for vision


def test_high_threshold_best_needs_intense_stimulus():
    weak = net().excite("summarise", capability="text", entry="strata", intensity=0.6)
    assert "best" not in weak.fired


def test_lateral_inhibition_motor_neighbours_dont_both_lead():
    # first motor neuron to fire inhibits the other; both may fire across
    # ticks in v0.1, but never in the same tick
    t = net().excite("fix the parser", capability="code")
    for tick in t.ticks:
        assert not ("opencode" in tick["fired"] and "pi" in tick["fired"])


def test_hebbian_success_strengthens_used_edges():
    n = net()
    t = n.excite("fix the parser", capability="code")
    assert t.edges_used
    edge = t.edges_used[0]
    before = n.synapses[edge]
    n.learn(t, success=True)
    assert n.synapses[edge] > before


def test_hebbian_failure_weakens_used_edges():
    n = net()
    t = n.excite("fix the parser", capability="code")
    edge = t.edges_used[0]
    before = n.synapses[edge]
    n.learn(t, success=False)
    assert n.synapses[edge] < before


def test_muse_code_recruited_for_end_to_end_code_work():
    n = net()
    assert "muse-code" in n.neurons
    assert n.neurons["muse-code"].region == "prefrontal"
    t = n.excite("build, test and ship this feature end to end", capability="code")
    assert "muse-code" in t.fired, t.fired


def test_weights_roundtrip(tmp_path):
    n = net()
    t = n.excite("fix the parser", capability="code")
    n.learn(t, success=True)
    out = tmp_path / "weights.json"
    n.save_weights(out)
    assert out.exists() and "->" in out.read_text()


def test_duplicate_synapse_edge_warns_instead_of_silently_dropping(tmp_path):
    # config/synapses.yaml lists muse-code->codex twice (+0.5 excitatory,
    # -0.3 lateral inhibition); the dict loader keeps only the later one.
    # A duplicate directed pair must warn so the dropped edge is visible.
    cfg = tmp_path / "config"
    shutil.copytree(CONFIG, cfg)
    with open(cfg / "synapses.yaml", "a", encoding="utf-8") as f:
        f.write("  - {from: strata, to: vision, weight: 0.5}\n")
        f.write("  - {from: strata, to: vision, weight: 0.5}\n")
    with pytest.warns(UserWarning, match="duplicate synapse strata->vision"):
        Network.from_config(cfg)
