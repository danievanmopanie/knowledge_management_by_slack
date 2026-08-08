# Knowledge Management by Slack

Fully **open-source** multi-agent knowledge management and operational support system that lives inside Slack.

## Channels

| Channel | Purpose |
|---------|---------|
| `#frontend-support` | Frontend Support knowledge agent – help teams resolve issues faster using organisational knowledge |
| `#inventory` | Inventory Management agent |
| `#work-management` | Multi-agent work management (Planner, Scheduler, Resource Coordinator) |
| `#knowledge-uploads` | Dedicated channel for uploading knowledge articles, incident notes, CSVs and runbooks |

## Key Characteristics

- 100% open source (no CopilotKit Channels credits or managed service dependencies)
- Local LLM inference on Asus GX10 (NVIDIA GB10)
- Local vector database + RAG for organisational knowledge
- Designed for development with Codex and clean Git workflows
- Extensible multi-agent architecture

## Documentation

- [Business Requirements Specification (BRS)](docs/BRS.md)

## Status

Early stage – BRS complete. Implementation starting with foundation (Slack Bolt + LangGraph + local LLM + RAG).

---

**Owner:** [Danie Ungerer](https://github.com/danievanmopanie)
