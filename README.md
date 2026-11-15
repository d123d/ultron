# Ultron

**The AI neural network. One brain, every agent and model Dustin has.**

Ultron is what AI Fleet became when the dispatcher was deleted. It is the
merge (2026-11-14) of Gemini's `ai-neural-network` (live firing, fleet
registry, leader election) and Muse's `neural-fleet` (propagation core,
docs, skill layer), plus the 20 improvements held for this merge.
`ai-neural-network` is the surviving repo; `neural-fleet` is archived
and points here.

There is no router. A task is a **stimulus**: it enters at sensory neurons,
spreads through weighted excitatory and inhibitory synapses, recruits a
**firing population** across cortical regions, the population does the
work, cerebellum checks verify it, and the outcome rewires the synapses.

| AI Fleet | Ultron | Brain |
|---|---|---|
| worker / model tier | neuron (`config/neurons.yaml`, 61) | neuron |
| router picks one worker | propagation; population decides | no central controller |
| task card | stimulus / signal | sensory input |
| capability list | receptive field | receptive field |
| rank | synaptic weights (learned) | synapse strength |
| cooldown / quota | refractory period + brainstem | refractory period |
| done-when checks | cerebellum -> learning signal | cerebellum |
| ledger | hippocampus: episodes | memory |
| doctor / health | brainstem homeostasis | brainstem |
| leader election | prefrontal leader neurons | executive control |

## Regions

- **sensory**: strata, vision, nim-vl, openrouter-gemma — local/private and visual input
- **memory**: utility, ollama-cloud, nim-lightning, openhuman — recall, fast tiers, unattended rhythm
- **association**: agy, main, gemini, gemini-api, gpt, meta, the NIM and Zen families, go/go-pro, muse — research and synthesis
- **prefrontal**: claude, codex, leaders (claude/codex/github), gemini-argon, agy-opus, muse-code, best — planning, review, executive
- **motor**: opencode, pi, github — code, edit, shell

Every worker and tier in the fleet registry has a neuron. `brain sync`
reports any registry entry that lacks one (neurogenesis check).

## Use

```bash
brain status
brain excite "fix the parser" --capability code --trace
brain excite "review the plan" --capability review --stakes high   # thalamic gating: prefrontal only
brain fire "summarise notes" --capability text                     # propagate + invoke + verify + learn
brain sleep                                                        # consolidation: decay + prune + save weights
brain sync                                                         # registry entries with no neuron
brain leader elect
```

MCP server: `brain-mcp` (also `ultron-mcp`). CLI also installs as `ultron`.

v0.4.0 adds: `brain providers` (who can fire today), `brain bakeoff --apply`
(eval scores -> synapses), `brain remember`/`recall` (embedding memory),
`brain spend` + cost-scaled learning, `brain ask --stream`,
`brain sync --add` (neurogenesis scaffolding), `brain weights merge|export`
(weights sync), and a code-enforced privacy gate on `brain fire --private`.

## Learning

Hebbian, with the merge's upgrades: **STDP-lite** (causal firing order
earns full update, reversed earns half), **neuromodulation** (a global
reward scalar scales the update), **learned inhibition** (inhibitory
edges relax on success, tighten on failure), and **sleep consolidation**
(unused weights decay; faded synapses prune to zero). Working memory:
`excite --carry` starts warm from the previous population.

## Cloud providers

Every major cloud provider has a direct tier + neuron (OpenAI, Anthropic,
Google, Mistral, Cohere, Groq, Cerebras, Together, Fireworks, DeepInfra,
SambaNova, DeepSeek, xAI, Perplexity, Moonshot, MiniMax, Hugging Face,
NVIDIA NIM, Ollama Cloud, OpenRouter, OpenCode Go), plus subscription/CLI
routes (Claude, Codex, Antigravity, Copilot, Muse). Azure, Bedrock, Vertex
and Cloudflare speak non-bearer APIs and run through the adapter layer
(`providers.py`: SigV4, api-key header, OAuth/express, account-id URL). Full matrix, keys and
fallbacks: `docs/PROVIDERS.md`. A tier fires only when its `key_env` is in
`~/.secrets/.env`; otherwise its fallback chain takes over.

## Skill layer

ponytail (minimum code), caveman (minimum tokens), find-skills
(skills.sh acquisition), plus the cloud provider skills —
openrouter-typescript-sdk, mmx-cli, cloud-providers — see
`docs/SKILLS.md` and `config/skills.yaml`.

## Access for other agents

See [ACCESS.md](ACCESS.md): repo link, clone, MCP config, and the
stimulus contract. One link is all an agent needs.
