from ai_neural_network.envelope import parse_valid_fleet_result, validate_handoff
from ai_neural_network.network import Network
from ai_neural_network.neuron import Neuron


def test_validate_handoff_valid_and_invalid():
    ok, errs = validate_handoff({"status": "ok", "summary": "done"})
    assert ok is True
    assert errs == []

    bad_ok, bad_errs = validate_handoff({"status": "unknown", "summary": ""})
    assert bad_ok is False
    assert len(bad_errs) >= 2


def test_parse_valid_fleet_result_valid_and_invalid():
    data, errs = parse_valid_fleet_result('FLEET_RESULT: {"status": "ok", "summary": "done"}')
    assert data == {"status": "ok", "summary": "done"}
    assert errs == []

    bad_data, bad_errs = parse_valid_fleet_result('FLEET_RESULT: {"status": "unknown", "summary": ""}')
    assert bad_data is None
    assert len(bad_errs) >= 2


def _two_neuron_network() -> Network:
    neurons = {
        "a": Neuron(name="a", region="test", threshold=0.3),
        "b": Neuron(name="b", region="test", threshold=0.3),
    }
    synapses = {("a", "b"): 0.8}
    return Network(neurons, synapses)


def test_coupling_tight_caps_fired_and_breaks_circuit():
    net = _two_neuron_network()
    trace = net.excite("a", intensity=1.0, coupling="tight")
    assert len(trace.fired) <= 1
    assert trace.circuit_broken is True


def test_max_neurons_circuit_breaker():
    net = _two_neuron_network()
    trace = net.excite("a", intensity=1.0, max_neurons=1)
    assert len(trace.fired) <= 1
    assert trace.circuit_broken is True
