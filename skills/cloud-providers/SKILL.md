---
name: cloud-providers
description: Ultron's cloud provider access — every direct API tier, subscription/CLI route, gateway, and catalog-only provider, with the env var, endpoint, and fallback for each. Use when adding a provider, debugging a dead tier, or choosing where a stimulus should fire.
---

# Cloud Providers — how Ultron reaches every cloud model

Ultron speaks one wire format: OpenAI-compatible `POST {base_url}/chat/completions`
with a Bearer key (`src/ai_neural_network/llm.py`). A provider is *direct* when it
offers that format; otherwise it is reached through a gateway (OpenRouter, NVIDIA
NIM, Ollama Cloud, OpenCode) or a subscription CLI, and listed as catalog-only
until someone writes an adapter. Full matrix: `docs/PROVIDERS.md`.

## Rules

1. **Keys never live in the repo.** Each tier names a `key_env`; the value lives in
   `~/.secrets/.env` (canonical) or the process env. Never commit, print, or log a key.
2. **A tier without its key fails fast** and its `fallback:` chain (workers.yaml)
   takes over. Adding a provider = add the tier + a neuron + synapses + a fallback.
3. **Check the catalog before trusting a model id:** `brain doctor --catalog`
   validates every tier's model against models.dev (the `catalog:` field) and
   OpenRouter's live list. Model ids in workers.yaml were verified 2026-11-14.
4. **Metered tiers are named, not auto.** Direct-API tiers cost money per call;
   free/flat tiers win automatic routing. Name a direct tier explicitly
   (`brain fire --tier openai-api ...`) or let fallbacks reach it.
5. **Private stimuli stay local.** `--private` work fires Strata/local neurons
   only; no cloud tier, gateway or direct, may receive it.

## Direct API tiers (key in ~/.secrets/.env)

| Tier | Provider | key_env |
|---|---|---|
| openrouter-ultra / openrouter-gemma / main | OpenRouter (300+ models, one key) | OPENROUTER_API_KEY |
| openai-api | OpenAI | OPENAI_API_KEY |
| anthropic-api | Anthropic (OpenAI-compat layer) | ANTHROPIC_API_KEY |
| gemini-api | Google AI Studio | GEMINI_API_KEY |
| mistral-api | Mistral AI | MISTRAL_API_KEY |
| cohere-api | Cohere (compat endpoint) | COHERE_API_KEY |
| groq-api | Groq | GROQ_API_KEY |
| cerebras-api | Cerebras | CEREBRAS_API_KEY |
| together-api | Together AI | TOGETHER_API_KEY |
| fireworks-api | Fireworks AI | FIREWORKS_API_KEY |
| deepinfra-api | DeepInfra | DEEPINFRA_API_KEY |
| sambanova-api | SambaNova | SAMBANOVA_API_KEY |
| deepseek-api | DeepSeek | DEEPSEEK_API_KEY |
| xai-api | xAI (Grok) | XAI_API_KEY |
| perplexity-api | Perplexity | PERPLEXITY_API_KEY |
| moonshot-api | Moonshot (Kimi) | MOONSHOT_API_KEY |
| minimax-api | MiniMax (+ `mmx-cli` skill for media) | MINIMAX_API_KEY |
| hf-api | Hugging Face router | HF_TOKEN |
| nim-* / vision / muse / meta | NVIDIA NIM | NVIDIA_API_KEY |
| utility / ollama-cloud | Ollama Cloud | OLLAMA_API_KEY |
| go | OpenCode Go | OPENCODE_GO_API_KEY |
| muse-* (36 tiers) | Muse model via 32 providers — full list in `docs/PROVIDERS.md` | per-tier (see matrix) |

## Subscription / CLI routes (no API key; needs the CLI logged in)

claude / best / claude-leader (Anthropic via `claude`), codex / gpt / codex-leader
(OpenAI via `codex`), agy / gemini / gemini-argon / agy-opus (Google via `agy`),
github / github-leader (GitHub Copilot via `copilot`), muse-code (`muse`),
openhuman (`openhuman`), opencode / pi (ACP agents).

## Adapter tiers (v0.4.0, `providers.py` — no plain bearer endpoint)

- **azure-api** — Azure OpenAI: `api-key` header + deployment in URL. Needs AZURE_OPENAI_ENDPOINT.
- **bedrock-api** — AWS Bedrock Converse, SigV4-signed (stdlib). Needs AWS_SECRET_ACCESS_KEY.
- **vertex-api** — Vertex AI: express API key (native format) or OAuth/gcloud + VERTEX_PROJECT (OpenAI-compat).
- **cloudflare-api** — Workers AI: `{account}` resolved from CLOUDFLARE_ACCOUNT_ID at call time.

Check who can actually fire with `brain providers` before routing a stimulus.

## Related skills in this repo

- `openrouter-typescript-sdk` — one key, 300+ models, TypeScript `callModel`.
- `mmx-cli` — MiniMax text/image/video/speech/search from the terminal.
