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
| NVIDIA NIM | nim-ultra, nim-mistral, nim-gemma, nim-vl, nim-minimax, nim-step, nim-glm, nim-lightning, vision, muse, meta | integrate.api.nvidia.com/v1 | NVIDIA_API_KEY | per-tier |
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

Every direct tier has a `fallback:` chain into free/flat tiers, so a missing
or exhausted key degrades instead of failing the stimulus.

## Subscription / CLI routes (no API key)

Anthropic via `claude` (claude, best, claude-leader) · OpenAI via `codex`
(codex, gpt, codex-leader) · Google via `agy` (agy, gemini, gemini-argon,
agy-opus) · GitHub Copilot via `copilot` (github, github-leader) ·
Meta Muse via `muse` (muse-code) · OpenHuman (openhuman) ·
OpenCode / Pi agents (opencode, pi) · local Strata (strata, tiny, local).

## Catalog-only — adapter needed, do not fake a tier

These have no OpenAI-compatible Bearer endpoint, so a direct tier would
only 401. Their models are reachable today via OpenRouter and NIM.

| Provider | Why not direct | Reachable via |
|---|---|---|
| Azure OpenAI | per-resource endpoint, `api-key` header, deployment names | OpenRouter, models.dev catalog `azure` |
| AWS Bedrock | SigV4 request signing | OpenRouter, catalog `amazon-bedrock` |
| Google Vertex AI | OAuth / service-account tokens | gemini-api (AI Studio), OpenRouter, catalog `google-vertex` |
| Cloudflare Workers AI | account id embedded in the URL | catalog `cloudflare-workers-ai` |

## Skills that operate the providers

- `cloud-providers` — this matrix as a skill, plus the key/fallback rules.
- `openrouter-typescript-sdk` — TypeScript control of 300+ models, one key.
- `mmx-cli` — MiniMax text/image/video/speech/search from the terminal.

Run `brain doctor --catalog` to re-verify every model id against models.dev.
