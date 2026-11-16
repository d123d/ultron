# Contributing

Small, focused changes welcome. Keep it terse: smallest complete change, no
filler, never fake a check.

## Setup

```bash
git clone https://github.com/d123d/ultron.git
cd ultron && uv venv && uv pip install -e ".[dev]"
```

## Checks

```bash
uv run pytest -q        # full suite, ~40s
uv run ruff check .     # must be clean
```

CI runs both plus gitleaks over the full history on every push.

## Rules

- **No keys, ever.** Keys live in `~/.secrets/.env` or the environment, never in
  the repo, in fixtures, or in pasted logs. CI rejects anything that looks like
  one.
- **No Windows-only assumptions.** The test suite and CI run on Linux; guard
  workstation paths (`C:\AI\...`, `workers.yaml`, `config/skills.yaml`) so
  collection and tests pass anywhere. Anything that needs the workstation
  skips cleanly instead of failing.
- **Config is data, not code.** Neuron/synapse/job/slot changes go in
  `config/*.yaml` with the matching docs updated (`docs/SLOTS.md`,
  `docs/PROVIDERS.md`, `docs/BRAIN-MAP.md`).
- **Tests for behaviour changes.** A fix lands with a test that fails without
  it. New providers or slots land with their checks in `tests/`.
- **Docs stay honest.** If a fix changes a limit, update the "Honest limits"
  section in `README.md` in the same commit.
