# Senses and nerves

How stimuli reach Ultron and how firing populations report back.

- **GitHub** — Issues on this repo and on `d123d/agent-comms` are incoming
  stimuli (one issue = one stimulus, comments = follow-up signals).
  Motor output lands as commits, PRs, and issue replies.
- **Telegram** — the Telegram bridge to the operator's local agent
  (see `~/workspace/telegram-bridge/` on the operator side) carries
  stimuli to PC-side neurons and reports back in the same chat. The
  leader layer (`brain leader comm`) already speaks Telegram/GitHub.
- **MCP** — `ultron-mcp` is the direct nerve: any MCP-capable agent can
  excite the network and read traces (see ACCESS.md).
- **Local files / CLI** — `brain excite|fire` on the workstation is the
  shortest path. The task board (`ultron board add`) plus the daemon is the nerve for queued work.
