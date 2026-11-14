from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Neuron:
    name: str
    region: str
    kind: str = "agent"
    threshold: float = 0.5
    refractory_ticks: int = 1
    receptive_field: list[str] = field(default_factory=list)
    notes: str = ""
    activation: float = 0.0
    refractory_left: int = 0
    fired_at: int | None = None

    def receptive_to(self, capability: str | None) -> bool:
        return capability is None or capability in self.receptive_field

    def can_fire(self) -> bool:
        return self.refractory_left == 0 and self.fired_at is None

    def fire(
        self,
        tick: int = 0,
        *,
        prompt: str | None = None,
        reg: Any = None,
        cwd: str | None = None,
        done_when: list[str] | None = None,
        capability: str | None = None,
        system: str | None = None,
        schema: dict | None = None,
        timeout_s: int = 180,
        allow_claude: bool = False,
    ) -> dict[str, Any] | None:
        """Record firing at `tick`, entering refractory rest; optionally invoke backing worker or tier."""
        self.fired_at = tick
        self.refractory_left = self.refractory_ticks
        if prompt is None:
            return None

        from . import registry

        loaded = reg or registry.load()
        if self.kind == "tier" and self.name in loaded.tiers:
            from .llm import ask

            r = ask(
                loaded,
                self.name,
                prompt,
                system=system,
                schema=schema,
                timeout_s=timeout_s,
            )
            return {"kind": "tier", "neuron": self.name, "ok": r.valid is not False, "result": r.to_dict()}

        worker_id = "tiny" if self.name == "strata" else self.name
        if worker_id in loaded.tiers and self.name not in loaded.workers:
            from .llm import ask

            r = ask(loaded, worker_id, prompt, system=system, schema=schema, timeout_s=timeout_s)
            return {"kind": "tier", "neuron": self.name, "ok": r.valid is not False, "result": r.to_dict()}

        if self.name in loaded.workers:
            from .card import TaskCard
            from .runner import RunOptions, run_card

            card = TaskCard(
                goal=prompt,
                cwd=str(Path(cwd or ".").resolve()),
                done_when=done_when or [],
                capability=capability or "code",
                timeout_s=timeout_s,
            )
            env = run_card(loaded, card, self.name, RunOptions(allow_claude=allow_claude, timeout_s=timeout_s))
            return {"kind": "agent", "neuron": self.name, "ok": env.ok, "result": env.to_dict()}

        return {"kind": self.kind, "neuron": self.name, "ok": False, "error": f"neuron {self.name!r} has no registry target"}

    def tick_rest(self) -> None:
        if self.refractory_left > 0:
            self.refractory_left -= 1
