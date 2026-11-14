# The skill layer

Neurons are *who* fires. Skills are *how* the firing behaves — learned
procedures layered over the regions, acquired without rewiring synapses.

- **ponytail** sits over prefrontal and motor. When the population's plan
  becomes code, ponytail's ladder decides how much code: skip it, reuse,
  stdlib, native feature, one line, minimum. It is why a strong network
  does not produce bloated output.
- **caveman** sits over all output. The FLEET_RESULT payload and any
  human-facing text leave in caveman register: facts whole, fluff gone.
- **find-skills** (skills.sh) sits over sensory and memory. It is how the
  network learns a genuinely new procedure: discover at skills.sh, vet,
  install into `skills/`, register in `config/skills.yaml`. New skill =
  new capability, no new neuron required.

Levels (lite / full / ultra) are per-skill in `config/skills.yaml`,
default full, matching how Dustin runs the fleet (ponytail ultra +
caveman ultra was the ai-fleet prompt setting from 2026-11-13).
