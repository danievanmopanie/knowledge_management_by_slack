# Knowledge Management by Slack

Local-first multi-agent knowledge management and operational support platform with **OpenBot as the agent control plane** and **Slack as the primary mobile collaboration surface**.

The project is migrating away from a Slack-centric model where channels directly select specialist agents. The target architecture registers durable coworkers in OpenBot, exposes them through AG-UI, and uses CopilotKit Channels to deliver one coherent AI team into Slack while keeping domain systems structured and private.

## Target collaboration surfaces

| Surface | Purpose |
|---------|---------|
| `#frontline-support` | Natural technician collaboration, troubleshooting, repeat-incident guidance and resolution capture. The current implementation still uses the `#frontend-support` name/configuration during migration. |
| `#create-knowledge` | Knowledge workshop for drafting, reviewing, curating and publishing reusable operational knowledge. |
| Slack DM with the app | Private technician coaching and safe troubleshooting using the same governed coworker team. |
| OpenBot native channels | Direct coworker interaction, deeper work, takeover and control-plane visibility. |
| Private domain web views | Bulk or specialist workflows that do not fit comfortably in mobile chat. |

Other domain capabilities such as inventory and work management remain part of the same control-plane direction rather than requiring independent Slack bots.

## Target architecture

```text
Slack / OpenBot UI / private web views
                │
                ▼
CopilotKit Channels + AG-UI
                │
                ▼
OpenBot multi-agent control plane
  Front Desk / Orchestrator
  Frontline Support coworker
  Knowledge coworker
  Inventory / Work / Builder / other specialists
                │
                ▼
Governed tools / MCP / APIs
                │
                ▼
Structured domain systems of record
```

### Principles

- Agents live in the control plane, not in Slack.
- Prefer one Slack app identity that can reach the whole coworker team.
- Slack channels express **human collaboration purpose**, not hard-coded agent ownership.
- Human identity flows from Slack through policy and into domain actions.
- OpenBot centralizes coworker identity, policy, audit and governed execution.
- Domain systems remain authoritative for knowledge, incidents, inventory and work records.
- Slack mobile is the default daily frontline experience; deeper web UI is an escape hatch, not a requirement.
- Local model inference and private/self-hosted domain services remain first-class design constraints.

## Current implementation

The repository still contains the working Slack-first implementation while the migration is designed and introduced incrementally:

- Slack Bolt runtimes and Socket Mode listeners;
- channel-ID-to-agent routing;
- Frontend Support collaborative thread logic;
- voice-note support;
- three-layer knowledge retrieval (governed knowledge, incident vectors and support graph);
- staged knowledge ingestion and confirmation;
- Block Kit interactivity and human confirmation flows;
- local LLM/RAG services on the GX10/GB10 platform.

The migration should preserve these useful behaviours while moving runtime, routing, state and governance boundaries into the target control-plane architecture.

## Documentation

- [Control Plane Collaboration UX](docs/CONTROL_PLANE_COLLABORATION_UX.md) — target `#frontline-support` and `#create-knowledge` UI/UX, interaction contract and migration sequence
- [Frontend Support Knowledge Architecture](docs/FRONTEND_SUPPORT_KNOWLEDGE_ARCHITECTURE.md)
- [Frontend Support Pilot](docs/FRONTEND_SUPPORT_PILOT.md)
- [Business Requirements Specification (BRS)](docs/BRS.md)

## Status

Active architecture migration. The current Slack-first implementation remains the compatibility baseline while OpenBot coworkers, AG-UI endpoints and CopilotKit Channels are introduced in staged implementation PRs.

---

**Owner:** [Danie Ungerer](https://github.com/danievanmopanie)
