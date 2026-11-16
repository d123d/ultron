# Ultron

One command line and one MCP server in front of every AI you can reach: cloud model APIs,
subscription CLIs, local models and coding-agent harnesses. You hand Ultron a job; it picks the
best available model or agent for that kind of job, falls through when one is down or out of
quota, checks the result, and learns from the outcome.

Ultron grew out of AI Fleet (a task-card dispatcher) and two brain-structured rewrites of it
(`ai-neural-network` and `neural-fleet`, merged 2026-11-14). The Python package is still named
`ai_neural_network`; the commands are `ultron` and `brain` (same program).

## Quick start

```bash
git clone https://github.com/d123d/ultron.git
cd ultron && uv venv && uv pip install -e ".[dev]"
uv run ultron slots        # what you can reach: providers, models, harnesses
uv run ultron jobs         # kinds of work and the models each one uses
uv run ultron job quick "Say hello"
```

Keys are read from `~/.secrets/.env` (or the environment) by the names in
`src/ai_neural_network/workers.yaml`. **No key belongs in this repo**: CI runs gitleaks over the
full history on every push, and run logs are redacted. A provider with no key simply stays
unavailable and its fallbacks answer.

## The three ideas

**Provider slots.** One slot per provider: endpoint or CLI login, concurrency, key state, and
every model it serves (`ultron slots --refresh` pulls the live `/models` lists). Any listed model
can be called without configuring it: `ultron ask "..." --tier "nvidia:<model id>,"`.
Local harnesses (Codex, OpenCode, Pi, Antigravity, Copilot, Claude Code, Muse, OpenHuman) have
slots too.

**Job slots.** `config/jobs.yaml` maps a kind of work to an ordered list of models or to an agent
harness. `ultron job <kind> "<prompt>"`:

| Kind | What runs |
|---|---|
| `quick`, `bulk`, `extract`, `draft` | fast free text models |
| `reason`, `plan`, `research` | strongest free reasoning and long-context models |
| `verify` | three providers answer, majority wins |
| `vision` | image QC (`--image`) |
| `private` | local model only; nothing leaves the machine |
| `leader`, `best` | subscription models |
| `code`, `review`, `investigate`, `local-code` | an agent harness works in `--cwd`, checked by `--done-when` |
| `unattended` | handed to OpenHuman's card queue |

**Board and daemon.** `ultron board add --text "<goal>" --cwd <repo> --done-when "<check>"` queues
agent work; `--dep` chains cards so they run one at a time; `--hold` parks one. `ultron daemon`
(a logon task on Windows) takes one ready card a minute, runs it on the first free harness, fails
over to the next harness on quota or crash, verifies with the card's checks and closes or reopens
it. Cards left in progress by a dead daemon are reopened on restart.

Details and the list of defects found in live use: [docs/SLOTS.md](docs/SLOTS.md).

## Under the hood: the network

Workers and model tiers are neurons in five regions (sensory, memory, association, prefrontal,
motor) joined by weighted excitatory and inhibitory synapses. A task is a stimulus: it spreads,
recruits a firing population, the population does the work, checks verify it, and the outcome
rewires the weights (Hebbian learning with STDP-lite, neuromodulation by cost, learned inhibition,
sleep consolidation). See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) and
[docs/BRAIN-MAP.md](docs/BRAIN-MAP.md).

```bash
ultron status
ultron excite "fix the parser" --capability code --trace   # who would fire
ultron fire "summarise notes" --capability text            # propagate, run, verify, learn
ultron leader elect                                        # which executive model is available
ultron doctor                                              # health and quota of every worker and tier
```

Shipped seed weights are `config/weights.json`; what a machine learns is saved in its state
directory, never in the repo.

## Skills and tools

Worker prompts carry the working rules (smallest complete change, terse output, never fake a
check) and, per task topic, the paths of matching skills from the machine's skill library plus its
tool registry: 3D worlds and assets, animation, cinema, fiction, audio, images, documents, code.
Topics are in `config/skills.yaml`; install skills with [skills.sh](https://skills.sh). A few
skills ship in `skills/`. See [docs/SKILLS.md](docs/SKILLS.md).

## Providers

Direct tiers for OpenRouter, NVIDIA NIM, Ollama Cloud, Google, OpenAI, Anthropic, Mistral, Cohere,
Groq, Cerebras, Together, Fireworks, DeepInfra, SambaNova, DeepSeek, xAI, Perplexity, Moonshot,
MiniMax, Hugging Face, OpenCode Go; adapters for Azure, Bedrock, Vertex and Cloudflare;
subscription routes through the `claude`, `codex`, `agy`, `copilot` and `muse` CLIs; local models
through any OpenAI-compatible server. Matrix: [docs/PROVIDERS.md](docs/PROVIDERS.md); vendor
quirks: [docs/PROVIDER-BEST-PRACTICES.md](docs/PROVIDER-BEST-PRACTICES.md).

## For other agents

[ACCESS.md](ACCESS.md): clone, MCP config and the worker contract. MCP tools include `brain_job`,
`brain_slots`, `brain_ask`, `brain_run`, `brain_fire`, `brain_doctor`, `brain_leader`.

## Honest limits

- Built and tested on one Windows 11 workstation; CI runs the tests on Linux. Paths in
  `workers.yaml`, `config/skills.yaml` and the board settings point at that machine: edit them.
- Job-slot model order is hand-set from live measurements, not learned yet.
- Health state is safe across threads and processes (file lock around `health.json`).

MIT licence.
