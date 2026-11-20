import threading
import time
from types import SimpleNamespace

import pytest
import yaml

from ai_neural_network import health, registry, throttle
from ai_neural_network.procs import FleetError
from ai_neural_network.registry import Registry, Tier
from ai_neural_network.throttle import config_for, provider_key, slot, spread_order

U = "http://127.0.0.1:11434/v1"
KEY = "127.0.0.1:11434/v1"


@pytest.fixture
def tstate(tmp_path, monkeypatch):
    monkeypatch.setenv("FLEET_STATE_DIR", str(tmp_path / "state"))
    return tmp_path / "state"


def _reg(throttle_cfg: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(throttle=throttle_cfg or {})


def test_provider_key(tstate):
    assert provider_key("http://127.0.0.1:8790/nvidia/v1") == "127.0.0.1:8790/nvidia"
    assert provider_key("http://127.0.0.1:11434/v1") == "127.0.0.1:11434/v1"
    assert provider_key("http://127.0.0.1:11434") == "127.0.0.1:11434"
    assert provider_key("https://api.example.com") == "api.example.com:443"
    assert provider_key("http://127.0.0.1:8790/nvidia/v1/") == "127.0.0.1:8790/nvidia"  # trailing slash
    assert provider_key("https://opencode.ai/zen/go/v1") == "opencode.ai:443/zen"  # direct Go tier


def test_first_request_never_waits(tstate):
    t0 = time.monotonic()
    with slot(_reg(), U):
        pass
    assert time.monotonic() - t0 < 0.9


def test_spacing_between_starts(tstate):
    with slot(_reg(), U):
        pass
    t0 = time.monotonic()
    with slot(_reg(), U):
        pass
    assert time.monotonic() - t0 >= 0.8  # default min_interval_s is 1


def test_concurrency_cap_blocks_until_release(tstate):
    reg = _reg({"default": {"min_interval_s": 0, "max_concurrent": 1, "max_queue_s": 5}})
    done = threading.Event()

    def grab():
        with slot(reg, U):
            done.set()

    worker = threading.Thread(target=grab, daemon=True)
    with slot(reg, U):
        worker.start()
        assert not done.wait(0.4)  # second request is parked while we hold the only slot
    assert done.wait(5)  # released -> it proceeds
    worker.join(5)  # its release must not land in the next test's state dir


def test_queue_overflow_fails(tstate):
    reg = _reg({"default": {"min_interval_s": 0, "max_concurrent": 1, "max_queue_s": 0.2}})
    with slot(reg, U):
        with pytest.raises(FleetError, match="busy"):
            with slot(reg, U):
                pass


def test_stale_in_flight_self_heals(tstate):
    now = time.monotonic()
    reg = _reg({"default": {"min_interval_s": 60, "max_concurrent": 1, "slot_ttl_s": 0.2}})
    throttle._save({KEY: {"starts": [now - 65], "last_start": now - 65}})  # crashed holder
    with slot(reg, U):  # stale start is pruned, slot is free
        pass


def test_registry_throttle_section(tstate, tmp_path, monkeypatch):
    p = tmp_path / "w.yaml"
    p.write_text(
        yaml.safe_dump({"throttle": {"default": {"min_interval_s": 5}, "x:1/y": {"max_concurrent": 7}}}), encoding="utf-8"
    )
    monkeypatch.setenv("FLEET_REGISTRY", str(p))
    reg = registry.load()
    assert reg.throttle["x:1/y"]["max_concurrent"] == 7
    assert config_for(reg, "http://x:1/y/v1")["min_interval_s"] == 5
    assert config_for(reg, "http://other:9/z/v1")["max_concurrent"] == 2  # library default
    p.write_text(yaml.safe_dump({"throttle": {"bad": {"nope": 1}}}), encoding="utf-8")
    with pytest.raises(ValueError, match="bad throttle"):
        registry.load()


def test_spread_order_prefers_idle_provider(tstate):
    reg = Registry(
        workers={},
        tiers={"a": Tier("a", "http://127.0.0.1:11/v1", "m", "free"), "b": Tier("b", "http://127.0.0.1:22/v1", "m", "free")},
        cost_order=["free"],
        chains={},
        board={},
    )
    now = time.monotonic()
    throttle._save({"127.0.0.1:11/v1": {"starts": [now], "last_start": now}})  # provider 11 is hot
    assert spread_order(reg, ["a", "b"]) == ["b", "a"]


def test_llm_unknown_tier_and_cooldown_fail_fast(tstate):
    from ai_neural_network.llm import candidates

    reg = Registry(workers={}, tiers={"t": Tier("t", U, "m", "free")}, cost_order=["free"], chains={}, board={})
    assert candidates(reg, "t") == ["t"]
    assert candidates(reg, "t,t") == ["t"]
    with pytest.raises(FleetError, match="unknown tier"):
        candidates(reg, "nope")
    health.set_cooldown("t", 600, "test")
    from ai_neural_network.llm import ask

    with pytest.raises(FleetError, match="cooldown"):
        ask(reg, "t", "hi")  # single cooled-down tier fails fast instead of hammering the provider
