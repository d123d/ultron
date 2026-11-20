# Cloud providers — the full access matrix

Verified live 2026-11-14 (Ultron review): model ids checked against
[models.dev](https://models.dev/api.json) and OpenRouter's live model list.
Ultron's wire format is OpenAI-compatible chat completions with a Bearer key
(`llm.py`); every direct tier below speaks it. Keys live in `~/.secrets/.env`
under the tier's `key_env` — never in this repo.

## Direct API (one tier + neuron each in workers.yaml / neurons.yaml)

| Provider | Tier | Endpoint | key_env | Model (2026-11-14) |
|---|---|---|---|---|
| OpenRouter | openrouter-ultra, openrouter-gemma, main | openrouter.ai/api/v1 | OPENROUTER_API_KEY | Nemotron 3 Ultra free / Gemma 4 31B free |
| OpenAI | openai-api | api.openai.com/v1 | OPENAI_API_KEY | gpt-5.5 |
| Anthropic | anthropic-api | api.anthropic.com/v1 (OpenAI-compat layer) | ANTHROPIC_API_KEY | claude-sonnet-5-5 |
| Google AI Studio | gemini-api | generativelanguage.googleapis.com/v1beta/openai | GEMINI_API_KEY | gemini-3.8-flash |
| NVIDIA NIM | nim-ultra, nim-mistral, nim-gemma, nim-vl, nim-minimax, nim-step, nim-glm, nim-lightning, nim-deepseek, vision, muse, meta | integrate.api.nvidia.com/v1 | NVIDIA_API_KEY | per-tier |
| Ollama Cloud | utility, ollama-cloud | ollama.com/v1 | OLLAMA_API_KEY | gpt-oss:120b |
| Mistral AI | mistral-api | api.mistral.ai/v1 | MISTRAL_API_KEY | mistral-large-latest |
| Cohere | cohere-api | api.cohere.com/compatibility/v1 | COHERE_API_KEY | command-a-03-2025 |
| Groq | groq-api | api.groq.com/openai/v1 | GROQ_API_KEY | openai/gpt-oss-120b |
| Cerebras | cerebras-api | api.cerebras.ai/v1 | CEREBRAS_API_KEY | gpt-oss-120b |
| Together AI | together-api | api.together.xyz/v1 | TOGETHER_API_KEY | openai/gpt-oss-120b |
| Fireworks AI | fireworks-api | api.fireworks.ai/inference/v1 | FIREWORKS_API_KEY | accounts/fireworks/models/deepseek-v4p1-flash |
| DeepInfra | deepinfra-api | api.deepinfra.com/v1/openai | DEEPINFRA_API_KEY | openai/gpt-oss-120b |
| SambaNova | sambanova-api | api.sambanova.ai/v1 | SAMBANOVA_API_KEY | gpt-oss-120b |
| DeepSeek | deepseek-api | api.deepseek.com/v1 | DEEPSEEK_API_KEY | deepseek-v4-pro |
| xAI | xai-api | api.x.ai/v1 | XAI_API_KEY | grok-4.5 |
| Perplexity | perplexity-api | api.perplexity.ai | PERPLEXITY_API_KEY | sonar-pro |
| Moonshot | moonshot-api | api.moonshot.ai/v1 | MOONSHOT_API_KEY | kimi-k2.6 |
| MiniMax | minimax-api | api.minimax.io/v1 | MINIMAX_API_KEY | MiniMax-M2.7 |
| Hugging Face | hf-api | router.huggingface.co/v1 | HF_TOKEN | moonshotai/Kimi-K2-Instruct |
| OpenCode Go | go | opencode.ai/zen/go/v1 | OPENCODE_GO_API_KEY | deepseek-v4.1-flash |
| Azure OpenAI (adapter) | azure-api | per-resource endpoint | AZURE_OPENAI_API_KEY | gpt-5.5 (deployment name) |
| AWS Bedrock (adapter) | bedrock-api | bedrock-runtime.<region>.amazonaws.com | AWS_ACCESS_KEY_ID | openai.gpt-oss-120b |
| Google Vertex (adapter) | vertex-api | aiplatform.googleapis.com | VERTEX_API_KEY | gemini-3.5-flash |
| Cloudflare (adapter) | cloudflare-api | api.cloudflare.com/.../accounts/{account}/ai/v1 | CLOUDFLARE_API_TOKEN | @cf/openai/gpt-oss-120b |

Every direct tier has a `fallback:` chain into free/flat tiers, so a missing
or exhausted key degrades instead of failing the stimulus.

## Subscription / CLI routes (no API key)

Anthropic via `claude` (claude, best, claude-leader) · OpenAI via `codex`
(codex, gpt, codex-leader) · Google via `agy` (agy, gemini, gemini-argon,
agy-opus) · GitHub Copilot via `copilot` (github, github-leader) ·
Meta Muse via `muse` (muse-code) · OpenHuman (openhuman) ·
OpenCode / Pi agents (opencode, pi) · local Strata (strata, tiny, local).

## Adapter tiers — direct since v0.4.0 (`src/ai_neural_network/providers.py`)

These four have no plain OpenAI-bearer endpoint, so they go through the
adapter layer: build (URL/headers/payload) + parse (response -> OpenAI shape).
Stdlib only, no new dependencies.

| Provider | Tier | How the adapter speaks it | Secrets needed |
|---|---|---|---|
| Azure OpenAI | azure-api | `api-key` header, deployment name in URL, `?api-version=` | AZURE_OPENAI_API_KEY + AZURE_OPENAI_ENDPOINT (model = deployment name) |
| AWS Bedrock | bedrock-api | Converse API, SigV4-signed by hand | AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY (AWS_REGION optional) |
| Google Vertex AI | vertex-api | Express: `x-goog-api-key` + native generateContent; OAuth: OpenAI-compat endpoint | VERTEX_API_KEY, or GOOGLE_ACCESS_TOKEN / gcloud + VERTEX_PROJECT |
| Cloudflare Workers AI | cloudflare-api | Bearer, `{account}` in URL resolved at call time | CLOUDFLARE_API_TOKEN + CLOUDFLARE_ACCOUNT_ID |

## Also new in v0.4.0

- `brain providers` — which tiers can fire today (key presence, adapter, cooldown).
- `brain bakeoff --tiers a,b --apply` — eval scores fed back into synapses.
- `brain remember` / `brain recall` — semantic memory via embedding tiers
  (openai-embed, gemini-embed).
- `brain spend` — estimated metered spend today; spend also scales the learn
  modulation in `brain fire` (cost neuromodulation, floor x0.2 at budget).
- `brain ask --stream` — SSE streaming on plain HTTP tiers.
- `brain sync --add NAME` — scaffold a neuron + hub synapse for a registry entry.
- `brain weights merge|export` — weights sync across brains/machines.
- `brain fire --private` — privacy gate enforced in code: local tiers only,
  workers refused (ask --private existed; fire now matches it).

## Skills that operate the providers

- `cloud-providers` — this matrix as a skill, plus the key/fallback rules.
- `openrouter-typescript-sdk` — TypeScript control of 300+ models, one key.
- `mmx-cli` — MiniMax text/image/video/speech/search from the terminal.

Run `brain doctor --catalog` to re-verify every model id against models.dev.
