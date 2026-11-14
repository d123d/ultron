# Migration from AI Fleet

**Stop:** thinking in routes. There is no `fleet route` and no single
right worker for a task. Ask what a stimulus should *recruit*.

**Carries over unchanged:** worker CLIs and probes, `~/.secrets/.env` key
names, done-when completion checks, the ledger format (it becomes the
episode store), and every worker's capability list (now receptive fields).

**Changes:**
1. `workers.yaml` -> split into `config/neurons.yaml` (the units) and
   `config/synapses.yaml` (the connections). Rank becomes seed weight.
2. One run can fire several neurons in sequence. Budgets and opt-in rules
   for expensive neurons are thresholds now, not router flags.
3. Outcomes feed back. A run is not finished when the receipt is filed;
   it is finished when `learn()` has updated the weights.

**Coexistence:** Neural Fleet does not modify AI Fleet. Run both, compare
populations vs routes on the same stimuli, keep whichever earns it.
