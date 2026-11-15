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

## Hand review, 2026-11-14 (public release)

- Repo is public. No API key is in it or its history (gitleaks over all commits; CI now runs gitleaks on every push). Keys stay in `~/.secrets/.env`.
- `redact()` only knew secrets in the process environment, but the keys live in `~/.secrets/.env`, so run logs, prompts and ledger errors were not redacted. Now both.
- Learned weights were saved into the tracked `config/weights.json`: every run dirtied the repo and test runs eroded shipped weights. Learning now saves to `<state>/weights.json`; `config/weights.json` is the shipped seed. `brain weights export` writes the live weights.
- `Network.fire` called `run_checks` with its arguments swapped (the check-only path could never pass).
- The handoff schema rejected the statuses the worker prompt asks for (`done`, `partial`); fixed, and a dead no-jsonschema fallback removed.
- `brain slots` lists local harnesses as slots too (driver, parallel slots, ready / cooldown / not installed).
- Muse worker is disabled until `muse login` is approved (CLI 1.4.20 installed, not logged in).
- Not hand-reviewed yet: `evals.py` task data, `gitinfo.py`, `worktree.py`, `throttle.py` lock internals.
- Known limits: `health.json` updates are thread-safe but not cross-process safe (daemon + CLI can lose an update); `ledger.read()` reads the whole file on every capped call.

## Vision QC gate (2026-11-15)

`ultron qc <folder> --since <minutes> --subject "<what it should show>"` looks at the newest
renders with the `vision` job slot and exits 1 on any FAIL, or when there is no new render. Use it
as a card check: `--done-when "ultron qc kits --since 120"`. A failed check sends the verdicts back
to the worker for one repair round.

Why: coding harnesses cannot see pictures. In the first ten world-project cards two workers
reported "done" on kits whose renders were black frames or a camera pressed against a grey wall;
the only check was "a commit exists". The gate agreed with a human look on every image tried
(3 of 5 Subsurface Colony renders failed, 3 of 3 BioDome renders passed).

Worker order from the same ten cards: Pi finished 5 of 5; OpenCode finished 2 and failed 5 at
start (its free model has no image input); Antigravity ran out of credits after 7 minutes.
Failover carried every card without an operator. Pi now goes ahead of OpenCode for code and edit.
