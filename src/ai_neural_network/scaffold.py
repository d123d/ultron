"""Neurogenesis scaffolding: append a neuron (+ hub synapse) for a registry name.

Text appends, not YAML round-trips — the config files keep their comments.
neurons.yaml is one flat mapping, so an indented entry at EOF lands inside it;
synapses.yaml is one list, same trick.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .paths import config_dir as default_config_dir
from .procs import FleetError


def add_neuron(name: str, *, region: str = "association", capabilities: list[str] | None = None,
               threshold: float = 0.5, config_dir: str | Path | None = None) -> dict[str, Any]:
    d = Path(config_dir) if config_dir else default_config_dir()
    nf, sf = d / "neurons.yaml", d / "synapses.yaml"
    text = nf.read_text(encoding="utf-8")
    if f"\n  {name}:" in text or text.startswith(f"neurons:\n  {name}:"):
        raise FleetError(f"neuron {name!r} already exists")
    caps = ", ".join(capabilities or ["text"])
    nf.write_text(text.rstrip() + f"""

  {name}:
    region: {region}
    kind: tier
    threshold: {threshold}
    refractory_ticks: 1
    receptive_field: [{caps}]
    notes: "Scaffolded by brain sync --add."
""", encoding="utf-8")
    stext = sf.read_text(encoding="utf-8")
    sf.write_text(stext.rstrip() + f"\n  - {{from: main, to: {name}, weight: 0.4}}\n", encoding="utf-8")
    return {"neuron": name, "region": region, "neurons_file": str(nf), "synapses_file": str(sf)}
