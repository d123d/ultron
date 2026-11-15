# Brain Map — AI Fleet, translated

| AI Fleet piece | Neural Fleet piece | Region / role |
|---|---|---|
| opencode worker | neuron `opencode` | motor |
| pi worker | neuron `pi` | motor |
| codex worker | neuron `codex` | prefrontal |
| claude worker | neuron `claude` | prefrontal (operator) |
| Muse Code (Meta, added 2026-11-14 — not from AI Fleet) | neuron `muse-code` | prefrontal (end-to-end coding agent, Muse Spark) |
| agy (Antigravity) | neuron `agy` | association |
| openhuman | neuron `openhuman` | memory / background rhythm |
| strata tiny/local tiers | neuron `strata` | sensory (local/private) |
| vision tier | neuron `vision` | sensory (visual cortex) |
| main tier | neuron `main` | association |
| best tier | neuron `best` | prefrontal (high threshold) |
| utility tier | neuron `utility` | memory / recall |
| router.py | *deleted* — propagation in `network.py` | — |
| workers.yaml rank | seed synaptic weights | synapses |
| task card | stimulus | input |
| capability | receptive field | input |
| cooldown / quota | refractory period | neuron state |
| done-when / verify | cerebellum check -> learning signal | cerebellum |
| ledger | episode record; weights are consolidated memory | hippocampus |
| doctor / health | brainstem homeostasis | brainstem |
| fleet CLI | `neuro` CLI: status / excite / learn | — |

## Added 2026-11-14 (Ultron review)

| Piece | Neuron | Region / role |
|---|---|---|
| muse-code worker (muse CLI) | neuron `muse-code` | prefrontal — now executable, was trace-only |
| openhuman worker | neuron `openhuman` | memory — now executable, was trace-only |
| strata tier (tiny/local aliases) | neuron `strata` | sensory — now executable, was trace-only |
| 16 direct cloud API tiers (OpenAI, Anthropic, Mistral, Cohere, Groq, Cerebras, Together, Fireworks, DeepInfra, SambaNova, DeepSeek, xAI, Perplexity, Moonshot, MiniMax, Hugging Face) | neurons `*-api` | association / memory / prefrontal — see docs/PROVIDERS.md |
| cloud provider skills (openrouter-typescript-sdk, mmx-cli, cloud-providers) | skill layer | all regions |
