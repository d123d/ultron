# Access Ultron: for agents

**Repo: https://github.com/d123d/ultron** (public, MIT). Old links to `d123d/ai-neural-network`
redirect here.

## Connect

1. **Clone it** (CLI, MCP server, config):
   ```bash
   git clone https://github.com/d123d/ultron.git
   cd ultron && uv venv && uv pip install -e .
   uv run ultron slots
   ```
2. **MCP**: add to your MCP config:
   ```json
   { "mcpServers": { "ultron": { "command": "uv", "args": ["run", "--directory", "<path-to-ultron>", "ultron-mcp"] } } }
   ```
3. **Bundled skills only** (ponytail, caveman, find-skills, openrouter-typescript-sdk, mmx-cli,
   cloud-providers): `npx skills add d123d/ultron`

## Give it work

- Pick a job slot, not a model: `ultron job quick|reason|plan|verify|vision|code "<prompt>"`
  (MCP: `brain_job`). `ultron jobs` lists the kinds.
- Agent work in a repo: `ultron job code "<goal>" --cwd <repo> --done-when "<check command>"`, or
  queue it: `ultron board add --text "<goal>" --cwd <repo> --done-when "<check>"`.
- A specific model: `ultron ask "<prompt>" --tier <tier>` or `--tier "<provider>:<model>,"`.
- See what is reachable right now: `ultron slots`, `ultron doctor`.

## The worker contract

- A worker gets one prompt file and a working directory. It ends its output with one line:
  `FLEET_RESULT: {"status": "done|partial|failed", "summary": "...", "files": [...], "notes": "..."}`
- Ultron trusts the card's `done-when` checks, not the worker's claim. A worker that edits the
  check it is judged by, or fakes the check's tool, fails the run.
- Outcomes rewire weights. Do not edit weights by hand.
- Keys live in `~/.secrets/.env`, by the names in `src/ai_neural_network/workers.yaml`.
  Never commit a key, never print one.
- `--private` and the `private` job stay on local models. Never route that work out.
