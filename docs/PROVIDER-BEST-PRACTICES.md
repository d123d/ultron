# Provider best practices (official docs, checked 2026-11-14)

Vendor docs only. Applied changes are in `src/ai_neural_network/workers.yaml`
(per-tier `extra:` dict is merged into every HTTP request body).

## Meta Muse
- Muse Code CLI 1.4.20, install `irm https://dev.meta.ai/install.ps1 | iex`; login `muse login` (device code).
- Headless: `muse exec --workspace <dir> --disable-approval --max-model-steps 200 "<prompt>"`.
  Headless cannot answer approval prompts; `--disable-approval` keeps the sandbox on (`--yolo` drops both: avoid).
- Exit 0 does not mean the work is correct: gate on tests. 1 failed, 2 usage error.
- `~/.config/muse/settings.json` needs `"schema_version": 1`. Default model `muse-spark-1.2`; `--reasoning-effort` default high.
- AGENTS.md / CLAUDE.md load only in a trusted workspace (`--trust-workspace`).
- Docs: https://dev.meta.ai/docs/muse-code/extending.md , https://dev.meta.ai/docs/muse-code/configuration.md
- Muse Glimmer 30B via NIM (`muse`, `meta` tiers): params unverified (model card JS-only).

## Anthropic (anthropic-api, OpenAI-compat layer)
- Compat layer is not production: no prompt caching, ignores `reasoning_effort`. temperature <1 and `json_object` return 400,
  so Ultron drops both for this tier (`llm.py`). Long term: native /v1/messages adapter. https://platform.claude.com/docs/en/api/openai-sdk

## OpenAI
- Flagship `gpt-6-astra`; Responses API is the main surface. codex exec: `--full-auto` removed, use `--sandbox workspace-write`.
  https://developers.openai.com/api/docs/models , https://learn.chatgpt.com/docs/non-interactive-mode

## Google
- Gemini OpenAI compat is beta; thinking cannot be disabled on Gemini 3/4. Never send `reasoning_effort` with `thinking_config`.
- Confirm ids via `GET /v1beta/openai/models` (`gemini-3.8-flash` unverified). Embeddings `gemini-embedding-2`. https://aistudio.google.com/docs/openai

## GitHub Copilot CLI
- `-p` headless denies every tool needing approval unless `--allow-all-tools`. Auth `COPILOT_GITHUB_TOKEN`; classic `ghp_` not supported.
  https://docs.github.com/en/copilot/how-tos/copilot-cli/automate-copilot-cli/run-cli-programmatically

## xAI / Perplexity
- xAI: `grok-4.7`. https://docs.x.ai/docs/models
- Perplexity: Sonar chat completions retired 2026-09-27; `perplexity-api` is dead until an Agent API (`/v1/agent`) adapter exists.

## NVIDIA NIM
- All tier ids live. Nemotron 3 Ultra: `chat_template_kwargs: {force_nonempty_content: true}` (set), `enable_thinking` toggles reasoning.
  Free tier about 40 RPM (forum, not official). https://docs.api.nvidia.com/nim/reference/nvidia-nemotron-3-ultra-550b-a55b

## OpenRouter / Ollama Cloud / OpenCode
- OpenRouter `:free`: 20 RPM; 50/day under 10 credits, 1000/day otherwise. Reasoning via `reasoning: {effort}`. https://openrouter.ai/docs/api-reference/limits
- Ollama Cloud: no `tool_choice`, no structured outputs; gpt-oss cannot disable thinking. https://docs.ollama.com/cloud
- OpenCode Go new base `https://opencode.ai/inference/go/openai/v1` (old still answers). Zen free models need the OpenCode client (bridge :4097). https://opencode.ai/docs/go/

## Other direct APIs
- Moonshot `kimi-k3.1-pro`; MiniMax `MiniMax-M3` with `reasoning_split: true` (temperature ignored); HF router `moonshotai/Kimi-K3.1-Pro`.
- Cohere `reasoning_effort` only none/high; newer model `command-a-plus-05-2026` (not switched: verify against key's /models).
- DeepSeek, Groq, Together, SambaNova, Cloudflare, OpenAI embeddings: match docs.
- Cerebras free tier caps gpt-oss-120b context at 65k.
- Bedrock Converse id `openai.gpt-oss-120b-1:0` (fixed). Azure: v1 GA path `/openai/v1/` needs no api-version (adapter still on 2024-10-21; works).
- Unverified, test before switching: Fireworks `deepseek-v4p1-flash` (404 page; quickstart default `glm-5p3-flash`), Vertex express `gemini-3.5-flash`.
