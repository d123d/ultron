# Architecture

## The one change that matters

AI Fleet: `stimulus -> router -> ONE worker -> check -> receipt`.
Ultron: `stimulus -> spread -> POPULATION -> check -> weight update`.

Everything else (workers, CLIs, keys, done-when checks) is reused. The
dispatcher was the only part replaced — because a brain has no dispatcher.

## Propagation (`Network.excite`)

1. **Input.** The stimulus lands on every neuron whose receptive field
   matches its capability, weighted by region: sensory 1.0, memory 0.8,
   association 0.65, motor 0.6, prefrontal 0.55. Input arrives at the
   senses first; the executive is recruited, it does not start the show.
2. **Ticks.** Each tick, every neuron at/above threshold and out of its
   refractory period fires. Firing is recorded in order — that order *is*
   the plan (plan neurons fire before motor neurons because the pathways
   take a tick to cross).
3. **Spread.** A firing neuron adds `weight x 1.0` to each target — spikes are
   all-or-none, however far above threshold the neuron was. Negative
   weights subtract (inhibition), and an inhibitory target in the same
   tick is suppressed before it can fire. Motor neurons are gated: they
   fire only for a stimulus inside their receptive field, while other
   regions can be recruited by spread alone.
4. **Leak.** Unfired activation decays x0.85 per tick, so a stimulus that
   recruits nobody dies out instead of echoing forever.
5. **Stop.** No neuron over threshold = the thought is finished.

## Inhibition

Two kinds, both already in `config/synapses.yaml`:
- **Lateral** (inside a region): opencode <-> pi, claude -> best. First to
  fire quiets its rival, so motor output is one clear channel, not two
  agents editing the same file.
- **Threshold gating**: `best` (0.85) and `claude` (0.70) sit high. Weak or
  routine stimuli physically cannot recruit them — the neural version of
  AI Fleet's `opt_in` / named-tier-only rules, except it emerges from the
  numbers instead of an if-statement.

## Refractory = cooldown

AI Fleet already had cooldowns for quota hits. Here they are refractory
periods: after firing, a neuron cannot fire again for N ticks. Same
protection, no special case in a router.

## Learning (`Network.learn`)

Hebbian, deliberately simple in v0.1:
- an outcome is reported once per run (`success=True/False`)
- positive edges on the firing path: +0.05 on success, -0.05 on failure
- every other positive edge: -0.002 decay (use it or lose it)
- inhibitory edges are structural and do not learn in v0.1
- weights clamp to [0, 1] and persist to `config/weights.json`

Committing `weights.json` is long-term potentiation made literal: the
network that starts tomorrow is the one yesterday's outcomes shaped.

## Cerebellum and brainstem (not neurons)

- **Cerebellum** = AI Fleet's completion checks (`--done-when`, verify).
  It compares intended vs actual output and is the source of the
  success/failure signal that drives learning.
- **Brainstem** = AI Fleet's `doctor`/health. Homeostasis: which neurons
  are alive, rested, and in quota. A neuron the brainstem marks down gets
  its threshold effectively raised until it recovers.

## What v0.1 does not do

- `Neuron.fire()` does not yet invoke the real worker CLI. Propagation and
  learning are real and tested; live invocation reuses AI Fleet adapters
  behind that one method.
- No recurrent loops between ticks beyond one-step spread, no spike-timing
  learning (STDP), no sleep/consolidation job yet. All three are natural
  next steps and none changes the config format.
