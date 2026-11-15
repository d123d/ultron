# Slots, jobs and the daemon (v0.5.0, 2026-11-14)

## Provider slots: one per provider

`brain slots` shows one row per provider: how it is reached (http, CLI login, adapter), its
concurrency (`slots`, from the `throttle:` section of `workers.yaml`), whether its key is present,
which tiers sit in it and how many models it serves.

- `brain slots --refresh` fetches every keyed HTTP provider's live `/models` list into
  `config/provider_models.json` (committed, so the list travels with the repo).
- `brain slots --models nvidia` prints every model of one provider.
- Any listed model is usable without a registry entry: `brain ask "..." --tier "nvidia:<model id>,"`.
  The one-off tier inherits the provider's endpoint, key, throttle and cost.
- A tier's provider is its `cli`, else a known local port (Strata :47831, model-gate :8790,
  Zen bridge :4097), else its `catalog` id. A new provider gets a slot by adding one tier.

## Job slots: the best model for each kind of work

`config/jobs.yaml` maps a kind of job to an ordered list of models. `brain job <kind> "<prompt>"`
tries them in order; a model that is cooled down, capped, out of credits or failing is skipped and
named in `skipped`. `brain jobs` lists the slots and fails on a job that names an unknown tier.

| Kind | Mode | What it is for |
|---|---|---|
| quick, bulk, extract, draft | ask | short answers, mass text, JSON, drafting: free fast models |
| reason, plan, research | ask | hard reasoning, plans, long-context synthesis |
| verify | vote | three providers answer, majority wins |
| vision | ask | image QC (`--image`) |
| private | ask | local Strata only |
| leader, best | ask | subscription models; `best` spends Claude/ChatGPT quota |
| code, review, investigate, local-code | run | an agent harness works in `--cwd`, checked by `--done-when` |
| unattended | run | handed to OpenHuman's card queue |

Agent jobs go through the worker chain (Codex, Antigravity, Copilot, OpenCode, Pi, Muse; Claude only
with `--allow-claude`). MCP: `brain_job`, `brain_slots`.

## Harnesses

| Harness | Into Ultron (does work) | Out of Ultron (uses it) |
|---|---|---|
| Codex, OpenCode, Pi | workers via acpx | MCP server `brain-mcp` / `C:\AI\bin\ultron.cmd` |
| Antigravity | worker `agy` + tiers gemini, gemini-argon, agy-opus | same |
| GitHub Copilot | worker `github` + tier github-leader | same |
| Claude Code | worker `claude` (opt-in) + tiers best, claude-leader | same |
| Muse | worker `muse-code` + tiers muse, meta | same |
| OpenHuman | worker `openhuman` (card queue, `oh_msg.py send`) | `ultron.cmd ask/job` from its shell |
| Strata | tiers strata, local, tiny; job `private` | Strata agent via MCP |

## Daemon: launches with Windows

Scheduled task `Ultron` (at logon) runs `C:\AI\bin\ultron.cmd daemon`. Every 60 s it takes one ready
card from the board (`brain board add ...`), runs it on the best free harness, verifies it and closes
or reopens it. Heartbeat: `<state>/daemon.json`. One sleep consolidation a day.

- `brain board add --text "<goal>" --cwd <repo> --done-when "<check>"` queues work.
- `--hold` parks a card (and everything that depends on it) until `brain board release <id>`.
- `--dep <id>` chains cards so they run strictly one after another.
- Three failures park a card with `fleet:needs-operator`; `board release` retries it.

## Found broken and fixed on 2026-11-14

- OpenHuman worker called a CLI that does not exist; now the handoff queue.
- `github-leader` tier crashed on every call (`CompletedProcess.rc`).
- HTTP 402 cooled one tier for 15 minutes although the whole account was out of credits; now every
  tier on that key cools for 6 hours. A rejected key (401/403) cools all its tiers too.
- Live that day: Ollama Cloud 402 on every model, Cerebras 402, SambaNova key rejected.
- Workers are told never to fake a missing check tool (one wrote a local `pytest.py`).
- OpenCode worker crashed at start: acpx ran a broken npx-cached `opencode-ai`. Fix is machine config, `~/.acpx/config.json` with `agents.opencode.argv` = the installed `opencode.exe acp`.
- Daemon heartbeat now says `working` while a card runs; worker prompts point at `ultron.cmd job vision` for render QC.
- First world-project card through the board: agy ran out of credits after 7 min, OpenCode crashed, Pi finished it in 440 s (failover worked without an operator).
