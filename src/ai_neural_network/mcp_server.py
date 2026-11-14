"""MCP server exposing ai-neural-network (`brain-mcp`) to MCP-capable agents."""
from __future__ import annotations

import json
from typing import Any

import anyio
from mcp.server.fastmcp import FastMCP

from . import health, leader, registry
from .card import TaskCard
from .doctor import doctor
from .llm import ask, vote
from .network import Network
from .runner import RunOptions, run_card

INSTRUCTIONS = (
    "AI Neural Network replaces flat AI Fleet dispatch with cortical propagation across regions "
    "(sensory, memory, association, prefrontal, motor), lateral inhibition, refractory cooldowns, "
    "cerebellum checks, and Hebbian plasticity. Use `brain_excite` to propagate without running, "
    "`brain_fire` to execute & learn, `brain_ask` for direct tier inference, `brain_run` for bounded worker "
    "cards (Claude remains operator-only over MCP), and `brain_leader` for leader election and GitHub/Telegram/Muse comms."
)

mcp = FastMCP("brain", instructions=INSTRUCTIONS)


def _j(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


async def _thread(fn, *a, **kw):
    return await anyio.to_thread.run_sync(lambda: fn(*a, **kw))


def _guard(fn):
    async def wrapper(*a, **kw):
        try:
            return await fn(*a, **kw)
        except Exception as e:  # noqa: BLE001 - return structured error instead of crashing server
            return _j({"error": f"{type(e).__name__}: {e}"})

    wrapper.__name__, wrapper.__doc__ = fn.__name__, fn.__doc__
    wrapper.__signature__ = __import__("inspect").signature(fn)
    return wrapper


@mcp.tool()
@_guard
async def brain_status() -> str:
    """Return cortical regions, neuron counts, synapse counts, cooled neurons, and active leader."""
    reg = registry.load()
    net = Network.from_config()
    by_region: dict[str, list[str]] = {}
    for n in net.neurons.values():
        by_region.setdefault(n.region, []).append(n.name)
    elect = leader.elect_leader(reg)
    return _j(
        {
            "regions": by_region,
            "neurons": len(net.neurons),
            "synapses": len(net.synapses),
            "inhibitory": sum(1 for w in net.synapses.values() if w < 0),
            "cooled": [name for name in net.neurons if health.in_cooldown(name)],
            "leader": elect["elected"],
            "leader_model": elect["model"],
        }
    )


@mcp.tool()
@_guard
async def brain_excite(
    stimulus: str,
    capability: str = "code",
    entry: str = "",
    intensity: float = 1.0,
    cooldowns: bool = False,
) -> str:
    """Inject a stimulus and return firing population trace across ticks without invoking workers."""
    net = Network.from_config()
    t = await _thread(
        lambda: net.excite(
            stimulus,
            capability=capability or None,
            entry=entry or None,
            intensity=intensity,
            respect_cooldowns=cooldowns,
        )
    )
    return _j(
        {
            "stimulus": stimulus,
            "capability": capability,
            "fired": t.fired,
            "ticks": t.ticks,
            "edges_used": [f"{a}->{b}" for a, b in t.edges_used],
        }
    )


@mcp.tool()
@_guard
async def brain_fire(
    stimulus: str,
    capability: str = "code",
    tier: str = "",
    worker: str = "",
    cwd: str = "",
    done_when: list[str] | None = None,
    dry_run: bool = False,
    timeout_s: int = 180,
) -> str:
    """Propagate stimulus, invoke the recruited tier or worker, verify checks, and update Hebbian weights."""
    if worker == "claude":
        return _j({"status": "skipped", "reason": "Claude is operator-only over MCP"})
    net = Network.from_config()
    res = await _thread(
        lambda: net.fire(
            stimulus,
            capability=capability or None,
            tier=tier or None,
            worker=worker or None,
            cwd=cwd or None,
            done_when=done_when,
            dry_run=dry_run,
            timeout_s=timeout_s,
        )
    )
    return _j(res)


@mcp.tool()
@_guard
async def brain_learn(edge: str = "", success: bool = True, rate: float = 0.05) -> str:
    """Apply Hebbian weight update to `A->B` synapse or run demo propagation learning check."""
    net = Network.from_config()
    if edge:
        if "->" not in edge:
            return _j({"error": "edge must use A->B syntax"})
        a, b = edge.split("->", 1)
        if (a, b) not in net.synapses:
            return _j({"error": f"unknown synapse {edge}"})
        before = net.synapses[(a, b)]
        delta = rate if success else -rate
        net.synapses[(a, b)] = max(0.0, min(1.0, before + delta))
        saved = await _thread(net.save_weights)
        return _j({"edge": edge, "before": round(before, 4), "after": round(net.synapses[(a, b)], 4), "saved": str(saved)})
    t = net.excite("fix the parser", capability="code")
    before = dict(net.synapses)
    net.learn(t, success=success, rate=rate)
    return _j(
        {
            "fired": t.fired,
            "changed": [
                {"edge": f"{a}->{b}", "before": before[(a, b)], "after": net.synapses[(a, b)]}
                for (a, b) in set(t.edges_used)
                if before[(a, b)] != net.synapses[(a, b)]
            ],
        }
    )


@mcp.tool()
@_guard
async def brain_ask(
    prompt: str,
    tier: str = "utility",
    system: str = "",
    schema_json: str = "",
    files: list[str] | None = None,
    images: list[str] | None = None,
    max_tokens: int = 0,
    timeout_s: int = 180,
    private: bool = False,
    majority_vote: bool = False,
) -> str:
    """Direct LLM call by tier (`utility`, `main`, `best`, `go-pro`, `gemini`, `muse`, `zen`, `vision`, `auto`)."""
    reg = registry.load()
    if majority_vote:
        r = await _thread(
            vote,
            reg,
            tier,
            prompt,
            system=system or None,
            files=files or [],
            max_tokens=max_tokens or None,
            timeout_s=timeout_s,
        )
        return _j(r.to_dict())
    schema = json.loads(schema_json) if schema_json else None
    r = await _thread(
        ask,
        reg,
        tier,
        prompt,
        system=system or None,
        schema=schema,
        files=files or [],
        images=images or [],
        max_tokens=max_tokens or None,
        timeout_s=timeout_s,
        private=private,
    )
    return _j(r.to_dict())


@mcp.tool()
@_guard
async def brain_run(
    goal: str,
    cwd: str,
    worker: str = "auto",
    paths: list[str] | None = None,
    done_when: list[str] | None = None,
    context_files: list[str] | None = None,
    constraints: list[str] | None = None,
    timeout_s: int = 1800,
    worktree: bool = False,
    capability: str = "code",
) -> str:
    """Dispatch one bounded task card to a worker and update network synaptic weights from verify result."""
    if worker == "claude":
        return _j({"status": "skipped", "reason": "Claude is operator-only over MCP"})
    reg = registry.load()
    card = TaskCard(
        goal=goal,
        cwd=cwd,
        paths=paths or [],
        done_when=done_when or [],
        context_files=context_files or [],
        constraints=constraints or [],
        timeout_s=timeout_s,
        capability=capability,
        worktree=worktree,
    )
    env = await _thread(run_card, reg, card, worker, RunOptions())
    net = Network.from_config()
    trace = net.excite(goal[:120], capability=capability, respect_cooldowns=True)
    net.learn(trace, success=env.ok)
    await _thread(net.save_weights)
    return env.to_json()


@mcp.tool()
@_guard
async def brain_doctor(live: bool = False, catalog: bool = False) -> str:
    """Brainstem homeostasis health/quota check across all workers and model tiers."""
    return _j(await _thread(doctor, registry.load(), live, catalog))


@mcp.tool()
@_guard
async def brain_leader(
    action: str = "elect",
    message: str = "",
    to: str = "leader",
    repo: str = "d123d/ai-neural-network",
    issue: int | None = None,
    read: bool = False,
    channel: str = "none",
) -> str:
    """Prefrontal leader election (`elect`), wakeup (`wake`), or cross-channel
    `comm` (github, telegram, muse, openhuman, leader).
    """
    reg = registry.load()
    if action == "elect":
        return _j(await _thread(leader.elect_leader, reg))
    if action == "wake":
        return _j(await _thread(lambda: leader.wake_leader(message, channel=channel, repo=repo, issue=issue, reg=reg)))
    if action == "comm":
        return _j(
            await _thread(
                lambda: leader.comm_message(to, message, repo=repo, issue=issue, read=read, channel=channel, reg=reg)
            )
        )
    return _j({"error": f"unknown action {action!r}"})


@mcp.tool()
@_guard
async def brain_workers() -> str:
    """List cortical neurons, worker agents, and model tiers."""
    reg = registry.load()
    net = Network.from_config()
    return _j(
        {
            "neurons": {k: vars(v) for k, v in net.neurons.items()},
            "workers": [w.__dict__ for w in reg.workers.values()],
            "tiers": [t.__dict__ for t in reg.tiers.values()],
        }
    )


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
