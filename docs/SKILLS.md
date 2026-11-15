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
default full, matching how the home machine runs it (ponytail ultra +
caveman ultra is the worker prompt setting).

## Cloud provider skills (added 2026-11-14, Ultron review)

- **cloud-providers** sits over all regions. It is the network's map of
  itself as a cloud client: every direct API tier, CLI route, and
  catalog-only provider, with key and fallback rules (`docs/PROVIDERS.md`).
- **openrouter-typescript-sdk** sits over association and memory. When a
  stimulus becomes code that must call cloud models, this is how: one
  OpenRouter key, 300+ models, `callModel`, tools, streaming.
- **mmx-cli** sits over association and sensory. MiniMax media (image,
  video, speech, transcription, search) fires through the `mmx` CLI;
  the `minimax-api` tier is its text route.

## The skill library (added 2026-11-14)

Ultron vendors only the six skills above. Everything else comes from the machine's own skill
library (on the home box: 450 skills, installed and updated with skills.sh). `config/skills.yaml`
has:

- `library`: the folder of installed skills (`ULTRON_SKILLS_DIR` overrides it).
- `always`: files every skilled prompt gets, such as the machine's tool registry.
- `topics`: keyword lists mapped to skill names: world-3d, animation, cinema, fiction, audio,
  image, documents, code, ai-systems.

When a card's goal matches a topic, the worker prompt ends with a `SKILLS` list of those
`SKILL.md` paths (ten at most) and the worker reads the ones that fit. A skill that is not
installed is skipped, so the same config works on a bare machine.

To add a capability: `npx skills add <owner/repo>`, read the `SKILL.md`, then add its name to a
topic. Installed this way on 2026-11-14: `humanizer` (blader/humanizer, MIT) for prose, and the
MiniMax set (minimax-ai/skills): music generation, playlists, the multimodal toolkit (speech,
image, video, transcription), docx, pdf, xlsx, pptx, vision analysis.
