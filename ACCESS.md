# Access Ultron — for agents

**Link: https://github.com/d123d/ultron** (private; agents working for
Dustin already have his GitHub access. Old links to
`d123d/ai-neural-network` redirect here.)

## Connect, three ways

1. **Clone it** (full network, CLI, MCP server):
   ```bash
   git clone https://github.com/d123d/ultron.git
   cd ultron && uv sync
   uv run brain status
   ```
2. **MCP** (send stimuli, read firing populations) — add to your MCP config:
   ```json
   { "mcpServers": { "ultron": { "command": "uv", "args": ["run", "--directory", "<path-to-ultron>", "ultron-mcp"] } } }
   ```
3. **Skills only** (ponytail / caveman / find-skills / openrouter-typescript-sdk / mmx-cli / cloud-providers, via skills.sh):
   ```bash
   npx skills add d123d/ultron
   ```

## The contract

- Send a stimulus: `brain excite "<task>" --capability <capability>` to see
  the population, `brain fire "<task>" --capability <capability>` to run it.
- Worker output ends with one `FLEET_RESULT` JSON line. That is the spike
  payload format; unmodified fleet workers are valid neurons.
- Outcomes rewire weights. If your run was checked, report the result —
  do not edit `config/weights.json` by hand.
- Keys live in `~/.secrets/.env` (names per tier in
  `src/ai_neural_network/workers.yaml`). Never commit a key.
- Cloud access: every major provider has a direct tier (see
  `docs/PROVIDERS.md`); a tier fires only when its key is present,
  otherwise its fallback chain answers. Azure / Bedrock / Vertex /
  Cloudflare are catalog-only (no bearer endpoint) — route via OpenRouter.
- `--private` work stays on local (Strata) neurons. Never route it out.
