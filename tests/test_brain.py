import json
from pathlib import Path

from ai_neural_network import Network
from ai_neural_network.cli import main
from ai_neural_network.mcp_server import mcp

CONFIG = Path(__file__).parent.parent / "config"


def test_network_all_regions_and_leader_neurons_loaded():
    net = Network.from_config(CONFIG)
    regions = {n.region for n in net.neurons.values()}
    assert {"sensory", "memory", "association", "prefrontal", "motor"} <= regions
    for name in (
        "claude-leader",
        "codex-leader",
        "gemini-argon",
        "github-leader",
        "agy-opus",
        "go-pro",
        "nim-ultra",
        "openrouter-ultra",
        "nim-mistral",
        "muse",
        "zen",
        "zen-deepseek",
        "zen-glm",
        "gemini",
    ):
        assert name in net.neurons, name


def test_network_fire_dry_run_and_pulse_alias(env):
    net = Network.from_config(CONFIG)
    res = net.fire("fix the parser", capability="code", dry_run=True)
    assert res["dry_run"] is True
    assert res["success"] is True
    assert res["population"]
    assert res["weights_saved"] is None
    pulse_res = net.pulse("inspect logs", capability="text", dry_run=True)
    assert pulse_res["success"] is True


def test_brain_cli_subcommands(env, capsys):
    assert main(["status", "--json"]) == 0
    st = json.loads(capsys.readouterr().out)
    assert st["neurons"] >= 26
    assert "regions" in st and "leader" in st

    assert main(["excite", "fix the parser", "--capability", "code", "--json"]) == 0
    ex = json.loads(capsys.readouterr().out)
    assert any(x in ex["fired"] for x in ("opencode", "pi"))

    assert main(["fire", "fix the parser", "--capability", "code", "--dry-run", "--json"]) == 0
    fr = json.loads(capsys.readouterr().out)
    assert fr["dry_run"] is True and fr["success"] is True

    assert main(["learn", "--demo", "--json"]) == 0
    lr = json.loads(capsys.readouterr().out)
    assert lr["changed"]


def test_brain_mcp_tools_registered():
    tools = set(mcp._tool_manager._tools.keys())
    expected = {
        "brain_status",
        "brain_excite",
        "brain_fire",
        "brain_learn",
        "brain_ask",
        "brain_run",
        "brain_doctor",
        "brain_leader",
        "brain_workers",
    }
    assert expected <= tools
