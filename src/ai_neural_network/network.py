from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import health, registry
from .neuron import Neuron
from .paths import config_dir as default_config_dir


@dataclass
class Trace:
    stimulus: str
    capability: str | None
    fired: list[str] = field(default_factory=list)
    ticks: list[dict[str, Any]] = field(default_factory=list)
    edges_used: list[tuple[str, str]] = field(default_factory=list)

    def __str__(self) -> str:
        return " -> ".join(self.fired) if self.fired else "(no neuron fired)"


class Network:
    """Activation spreads across cortical regions; no central dispatcher."""

    def __init__(
        self,
        neurons: dict[str, Neuron],
        synapses: dict[tuple[str, str], float],
        config_dir: Path | None = None,
    ):
        self.neurons = neurons
        self.synapses = dict(synapses)
        self.config_dir = config_dir

    @classmethod
    def from_config(cls, config_dir: str | Path | None = None) -> Network:
        d = Path(config_dir) if config_dir else default_config_dir()
        ndata = yaml.safe_load((d / "neurons.yaml").read_text(encoding="utf-8"))["neurons"]
        neurons = {name: Neuron(name=name, **cfg) for name, cfg in ndata.items()}
        sdata = yaml.safe_load((d / "synapses.yaml").read_text(encoding="utf-8"))["synapses"]
        synapses = {(s["from"], s["to"]): float(s["weight"]) for s in sdata}
        weights_file = d / "weights.json"
        if weights_file.exists():
            learned = json.loads(weights_file.read_text(encoding="utf-8"))
            for key, w in learned.items():
                if "->" in key:
                    a, b = key.split("->", 1)
                    if (a, b) in synapses:
                        synapses[(a, b)] = float(w)
        return cls(neurons, synapses, config_dir=d)

    def save_weights(self, path: str | Path | None = None) -> Path:
        target = Path(path) if path else ((self.config_dir or default_config_dir()) / "weights.json")
        data = {f"{a}->{b}": round(w, 4) for (a, b), w in sorted(self.synapses.items())}
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        return target

    def apply_brainstem(self) -> list[str]:
        """Raise effective threshold of neurons whose worker/tier is in cooldown."""
        cooled = []
        for name, n in self.neurons.items():
            if health.in_cooldown(name):
                n.refractory_left = max(n.refractory_left, n.refractory_ticks)
                cooled.append(name)
        return cooled

    def excite(
        self,
        stimulus: str,
        capability: str | None = None,
        entry: str | None = None,
        intensity: float = 1.0,
        max_ticks: int = 8,
        *,
        respect_cooldowns: bool = False,
        stakes: str | None = None,
        carry: bool = False,
    ) -> Trace:
        for n in self.neurons.values():
            n.activation, n.fired_at, n.refractory_left = 0.0, None, 0
        if respect_cooldowns:
            self.apply_brainstem()
        trace = Trace(stimulus=stimulus, capability=capability)
        entry_boost = {
            "sensory": 1.0,
            "memory": 0.8,
            "association": 0.65,
            "prefrontal": 0.55,
            "motor": 0.6,
        }
        if entry:
            if entry not in self.neurons:
                raise KeyError(f"unknown entry neuron: {entry!r}")
            self.neurons[entry].activation = intensity
        else:
            for n in self.neurons.values():
                if n.receptive_to(capability):
                    n.activation = intensity * entry_boost.get(n.region, 0.6)
        if carry:
            # Working memory: last population stays warm, so a follow-up
            # stimulus starts from the previous train of thought.
            for name in getattr(self, "_last_fired", []):
                if name in self.neurons:
                    self.neurons[name].activation += 0.3

        for tick in range(max_ticks):
            # Thalamic gating: high stakes raises every non-prefrontal
            # threshold, so only the executive is recruited.
            gate = 1.4 if stakes == "high" else 1.0
            ready = [
                n
                for n in self.neurons.values()
                if n.can_fire()
                and n.activation >= n.threshold * (gate if n.region != "prefrontal" else 1.0)
                and (n.region != "motor" or n.receptive_to(capability))
            ]
            if not ready:
                break
            ready.sort(key=lambda n: -n.activation)
            suppressed: set[str] = set()
            fired_now: list[Neuron] = []
            tick_info: dict[str, Any] = {"tick": tick, "fired": []}
            for n in ready:
                if n.name in suppressed:
                    continue
                n.fire(tick)
                fired_now.append(n)
                trace.fired.append(n.name)
                tick_info["fired"].append(n.name)
                for (a, b), w in self.synapses.items():
                    if a == n.name and w < 0:
                        suppressed.add(b)
            trace.ticks.append(tick_info)
            for n in fired_now:
                for (a, b), w in self.synapses.items():
                    if a != n.name or b not in self.neurons:
                        continue
                    target = self.neurons[b]
                    if target.fired_at is not None:
                        continue
                    target.activation += w
                    if w > 0:
                        trace.edges_used.append((a, b))
            for n in self.neurons.values():
                n.tick_rest()
                if n.fired_at is None:
                    n.activation *= 0.85
        self._last_fired = list(trace.fired)
        return trace

    def learn(
        self,
        trace: Trace,
        success: bool,
        rate: float = 0.05,
        decay: float = 0.002,
        modulation: float = 1.0,
    ) -> None:
        """Hebbian + STDP-lite + neuromodulation (Ultron merge, 2026-11-14).

        - Excitatory edges on the firing path move by rate x modulation,
          full when the source fired no later than the target (causal
          order), half when the order was reversed.
        - modulation is the global reward signal: callers scale it with
          outcome quality (verify margin, cost, quota pain).
        - Inhibitory edges learn too: a fired inhibitory source relaxes
          by 0.01 on success, tightens by 0.01 on failure, bounded.
        - Unused excitatory edges decay.
        """
        tick_of: dict[str, int] = {}
        for tick in trace.ticks:
            for name in tick.get("fired", []):
                tick_of.setdefault(name, tick.get("tick", 0))
        used = set(trace.edges_used)
        for edge, w in list(self.synapses.items()):
            a, b = edge
            if w > 0:
                if edge in used:
                    causal = 1.0 if tick_of.get(a, 0) <= tick_of.get(b, 0) else 0.5
                    delta = rate * modulation * causal
                    w = w + delta if success else w - delta
                else:
                    w = w - decay
                self.synapses[edge] = max(0.0, min(1.0, w))
            elif a in tick_of:
                w = w + 0.01 if success else w - 0.01
                self.synapses[edge] = max(-1.0, min(-0.05, w))

    def sleep(self, decay: float = 0.01, prune_below: float = 0.05) -> dict[str, Any]:
        """Sleep consolidation: decay every excitatory weight, prune the
        ones that faded below prune_below to 0, persist weights.json."""
        pruned: list[str] = []
        for edge, w in list(self.synapses.items()):
            if w <= 0:
                continue
            w = max(0.0, w - decay)
            if 0 < w < prune_below:
                pruned.append(f"{edge[0]}->{edge[1]}")
                w = 0.0
            self.synapses[edge] = w
        path = self.save_weights()
        return {"pruned": pruned, "weights_saved": str(path)}

    def sync_missing(self) -> list[str]:
        """Neurogenesis check: registry workers/tiers with no neuron yet.
        tiny/local are Strata aliases and count as covered by `strata`."""
        reg = registry.load()
        known = set(self.neurons) | {"tiny", "local"}
        return sorted((set(reg.workers) | set(reg.tiers)) - known)

    def _select_target(self, trace: Trace, reg: registry.Registry, *, capability: str | None, allow_claude: bool) -> str | None:
        for name in reversed(trace.fired):
            if name in reg.workers:
                w = reg.workers[name]
                if w.enabled and (allow_claude or not w.opt_in) and not health.in_cooldown(name):
                    if capability is None or capability in w.capabilities:
                        return name
            if name in reg.tiers and not health.in_cooldown(name):
                return name
        for name in reversed(trace.fired):
            if name in reg.tiers or name in reg.workers:
                return name
        return None

    def fire(
        self,
        stimulus: str,
        *,
        capability: str | None = None,
        entry: str | None = None,
        intensity: float = 1.0,
        tier: str | None = None,
        worker: str | None = None,
        cwd: str | None = None,
        done_when: list[str] | None = None,
        system: str | None = None,
        schema: dict | None = None,
        timeout_s: int = 180,
        allow_claude: bool = False,
        dry_run: bool = False,
        save_path: str | Path | None = None,
        reg: registry.Registry | None = None,
    ) -> dict[str, Any]:
        """Propagate stimulus, execute via recruited neuron (unless dry_run), run cerebellum checks, and update weights."""
        loaded = reg or registry.load()
        trace = self.excite(
            stimulus,
            capability=capability,
            entry=entry,
            intensity=intensity,
            respect_cooldowns=not dry_run,
        )
        chosen = worker or tier or self._select_target(trace, loaded, capability=capability, allow_claude=allow_claude)
        result: dict[str, Any] | None = None
        success = bool(trace.fired)

        if not dry_run:
            if worker:
                from .card import TaskCard
                from .runner import RunOptions, run_card

                card = TaskCard(
                    goal=stimulus,
                    cwd=str(Path(cwd or ".").resolve()),
                    done_when=done_when or [],
                    capability=capability or "code",
                    timeout_s=timeout_s,
                )
                env = run_card(loaded, card, worker, RunOptions(allow_claude=allow_claude, timeout_s=timeout_s))
                success = env.ok
                result = env.to_dict()
            elif tier:
                from .llm import ask

                r = ask(loaded, tier, stimulus, system=system, schema=schema, timeout_s=timeout_s)
                success = r.valid is not False
                result = r.to_dict()
            elif chosen and chosen in self.neurons:
                inv = self.neurons[chosen].fire(
                    len(trace.ticks),
                    prompt=stimulus,
                    reg=loaded,
                    cwd=cwd,
                    done_when=done_when,
                    capability=capability,
                    system=system,
                    schema=schema,
                    timeout_s=timeout_s,
                    allow_claude=allow_claude,
                )
                success = bool(inv and inv.get("ok"))
                result = inv
            elif done_when:
                from .verify import run_checks

                v = run_checks(Path(cwd or ".").resolve(), done_when)
                success = bool(v.get("passed"))
                result = {"kind": "cerebellum", "verify": v}

            self.learn(trace, success=success)
            weights_file = self.save_weights(save_path)
        else:
            weights_file = None

        return {
            "stimulus": stimulus,
            "capability": capability,
            "population": trace.fired,
            "trace": str(trace),
            "ticks": trace.ticks,
            "edges_used": [f"{a}->{b}" for a, b in trace.edges_used],
            "chosen": chosen,
            "success": success,
            "dry_run": dry_run,
            "weights_saved": str(weights_file) if weights_file else None,
            "result": result,
        }

    pulse = fire
