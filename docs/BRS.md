# Business Requirements Specification (BRS)

**Project Name:** Knowledge Management by Slack  
**Repository:** `knowledge_management_by_slack`  
**Version:** 1.0  
**Date:** 8 August 2026  
**Author:** Danie Ungerer  
**Status:** Draft for Implementation

---

## 1. Executive Summary

This document defines the business requirements for a fully open-source, self-hosted multi-agent knowledge management and operational support system that lives inside Slack.

The solution enables specialised AI agents to operate in dedicated Slack channels, allowing teams to:

- Resolve frontend support issues faster using organisational knowledge
- Manage inventory through conversational interaction
- Coordinate work planning, scheduling and resource allocation
- Continuously contribute and reuse knowledge through a dedicated upload channel

All processing (LLM inference, embeddings, vector storage, and agent orchestration) runs locally on the organisation’s Asus GX10 (NVIDIA GB10) infrastructure. No proprietary managed channel services (e.g. CopilotKit Intelligence) are used.

---

## 2. Business Objectives

1. **Improve knowledge reuse** – Convert existing knowledge articles, incident resolution notes and operational documents into searchable, reusable organisational knowledge available inside Slack.
2. **Accelerate issue resolution** – Enable Frontend Support teams to get accurate, context-aware answers and guidance directly in Slack channels.
3. **Specialise AI assistance** – Provide domain-specific agents for different operational needs instead of a single generic bot.
4. **Maintain full data control** – Keep all models, embeddings, knowledge and conversation data on-premises / on the GX10.
5. **Create a repeatable and extensible platform** – Build a clean, open-source foundation that can be improved with Codex and extended with additional agents over time.

---

## 3. Scope

### 3.1 In Scope

- Four dedicated Slack channels with specialised agent behaviour:
  - `#frontend-support`
  - `#inventory`
  - `#work-management`
  - `#knowledge-uploads`
- Fully open-source stack (Slack Bolt + LangGraph + local LLM + local vector database)
- Local LLM inference on the Asus GX10
- Retrieval-Augmented Generation (RAG) using a local vector database
- Ability to upload knowledge articles, incident CSVs, runbooks and related documents via the `#knowledge-uploads` channel
- Thread-aware conversations
- Basic human-in-the-loop confirmation for important actions
- Git-based development workflow optimised for Codex

### 3.2 Out of Scope (Initial Release)

- Polished generative UI components equivalent to commercial solutions (can be added later)
- Multi-tenant / multi-organisation support
- Advanced analytics dashboard
- Native mobile applications
- Integration with external SaaS ticketing systems beyond basic tools (can be added later)

---

## 4. Stakeholders

| Role                        | Interest                                      |
|----------------------------|-----------------------------------------------|
| Frontend Support Teams     | Faster, higher-quality issue resolution       |
| Inventory / Asset teams    | Conversational inventory queries and updates  |
| Work / Operations planners | Planning, scheduling and resource coordination|
| Knowledge owners           | Easy contribution of knowledge assets         |
| IT Operations / Platform   | Reliable, secure, maintainable local system   |
| Project Owner (Danie)      | Extensible platform aligned with local AI strategy |

---

## 5. Channel & Agent Mapping

| Slack Channel            | Primary Agent(s)                          | Main Responsibilities |
|--------------------------|-------------------------------------------|-----------------------|
| `#frontend-support`      | Frontend Support Knowledge Agent          | Answer support questions, retrieve relevant knowledge, guide troubleshooting, summarise threads |
| `#inventory`             | Inventory Management Agent                | Query stock/assets, track items, provide status, support basic inventory operations |
| `#work-management`       | Work Management Orchestrator + specials   | Planner, Scheduler and Resource Coordinator agents working together |
| `#knowledge-uploads`     | Knowledge Ingest Agent                    | Accept uploaded documents/CSVs, process them, and add them to the knowledge base |

---

## 6. Functional Requirements

### 6.1 General Requirements (All Channels)

- FR-01: The system shall respond when mentioned (`@bot`) or when configured to listen in the channel.
- FR-02: The system shall maintain conversation context within a Slack thread.
- FR-03: The system shall use a local large language model running on the GX10.
- FR-04: The system shall retrieve relevant organisational knowledge via RAG before answering when appropriate.
- FR-05: The system shall be fully open-source and free of proprietary managed channel credit limits.

### 6.2 `#frontend-support` Channel

- FR-10: The agent shall answer technical and process questions related to frontend support using the knowledge base.
- FR-11: The agent shall cite sources from the knowledge base when used.
- FR-12: The agent shall be able to summarise long threads or incident discussions.
- FR-13: The agent shall escalate or clearly state when it does not have sufficient knowledge.

### 6.3 `#inventory` Channel

- FR-20: The agent shall answer queries about inventory status, location and availability.
- FR-21: The agent shall support structured inventory-related questions and simple updates (subject to confirmation).
- FR-22: The agent may maintain or query a dedicated inventory knowledge/store as required.

### 6.4 `#work-management` Channel

- FR-30: The system shall support a multi-agent pattern consisting of at least:
  - Planner Agent
  - Scheduler Agent
  - Resource Coordinator Agent
- FR-31: A coordinating agent shall be able to delegate sub-tasks to the specialist agents.
- FR-32: The agents shall assist with breaking down work, proposing schedules, and identifying resource needs.

### 6.5 `#knowledge-uploads` Channel

- FR-40: Users shall be able to upload knowledge articles, incident resolution notes, CSVs, PDFs, Markdown and similar documents.
- FR-41: The system shall detect file uploads in this channel.
- FR-42: The system shall request confirmation (and optionally metadata) before ingesting.
- FR-43: On confirmation, the system shall chunk, embed and store the content in the local vector database.
- FR-44: Original files shall be retained in a controlled local raw document store for audit and re-processing.

---

## 7. Non-Functional Requirements

| ID     | Category          | Requirement |
|--------|-------------------|-----------|
| NFR-01 | Privacy & Security| All model inference, embeddings and knowledge storage shall remain on the local GX10 infrastructure. |
| NFR-02 | Open Source       | The entire solution shall be open-source with no dependency on paid managed channel services. |
| NFR-03 | Performance       | Responses should typically complete within acceptable interactive time for Slack usage (target < 30–60 seconds for most queries). |
| NFR-04 | Reliability       | The system shall be runnable as a long-lived service with clear restart and recovery procedures. |
| NFR-05 | Maintainability   | The codebase shall be structured for development with Codex and clear separation of agents, tools and knowledge layers. |
| NFR-06 | Extensibility     | It shall be straightforward to add new specialised agents or new channels. |
| NFR-07 | Observability     | Basic logging of agent decisions, tool calls and ingest events shall be available. |

---

## 8. High-Level Architecture

```
Slack Workspace
  ├── #frontend-support
  ├── #inventory
  ├── #work-management
  └── #knowledge-uploads
          │
          ▼
Slack Bolt Application (Router + Event Handlers)
          │
          ▼
Agent Orchestration Layer (LangGraph)
  ├── Frontend Support Agent
  ├── Inventory Agent
  ├── Work Management Orchestrator
  │     ├── Planner
  │     ├── Scheduler
  │     └── Resource Coordinator
  └── Knowledge Ingest Agent
          │
          ├── Local LLM (Ollama / vLLM on GX10)
          └── Knowledge Layer
                ├── Vector Database (Chroma or Qdrant)
                ├── Local Embeddings
                └── Raw Document Store
```

---

## 9. Technology Preferences

| Area                    | Preferred Choice                          | Notes |
|-------------------------|-------------------------------------------|-------|
| Slack SDK               | Slack Bolt (Python)                       | Official and mature |
| Agent Framework         | LangGraph                                 | Strong continuity with previous OpenTag exploration |
| LLM Serving             | Ollama or vLLM on GX10                    | Local, high-performance |
| Vector Database         | Chroma (initial) → Qdrant (later)         | Start simple, scale later |
| Embeddings              | Local model via Ollama                    | Keep data local |
| Conversation State      | LangGraph Checkpointer (SQLite → Postgres) | Simple to start |
| Runtime                 | Docker Compose or systemd on GX10         | Repeatable deployment |

---

## 10. Success Criteria

The solution will be considered successful when:

1. Users in `#frontend-support` can obtain useful, knowledge-grounded answers that help resolve issues faster.
2. Documents uploaded to `#knowledge-uploads` become searchable and usable by the other agents.
3. The system runs reliably on the GX10 without external managed channel dependencies.
4. New specialised agents can be added with moderate effort.
5. The codebase remains clean and Codex-friendly.

---

## 11. Assumptions

- The Asus GX10 with GB10 Superchip and 128 GB unified memory remains the primary inference platform.
- Slack workspace allows installation of custom apps and has sufficient app slots.
- Initial knowledge corpus will be curated (quality over quantity).
- Users will be trained on how to interact with the agents and the upload channel.

---

## 12. Risks & Mitigations

| Risk                              | Impact | Mitigation |
|-----------------------------------|--------|----------|
| Local model quality/speed         | Medium | Start with strong models (e.g. Qwen3 30B-A3B class); allow model swapping |
| Poor retrieval quality            | High   | Careful chunking, good source documents, citation of sources |
| Low adoption                      | High   | Focus on real value in `#frontend-support` first; keep UX simple |
| Operational overhead of self-hosting | Medium | Clear runbooks, Docker Compose, health checks |
| Scope creep across agents         | Medium | Strict channel separation and phased delivery |

---

## 13. Phased Delivery Recommendation

**Phase 1 – Foundation**
- Core Slack Bolt + LangGraph skeleton
- Local LLM integration
- `#frontend-support` agent + basic RAG
- `#knowledge-uploads` ingest flow

**Phase 2 – Expansion**
- `#inventory` agent
- Improved knowledge quality and citation
- Basic human-in-the-loop confirmations

**Phase 3 – Multi-Agent Work Management**
- `#work-management` with Planner / Scheduler / Resource Coordinator pattern

**Phase 4 – Hardening & Extensibility**
- Better observability, deployment packaging, additional agents as needed

---

## 14. Open Questions

1. Preferred initial vector database (Chroma vs Qdrant)?
2. Preferred local model for general vs specialised agents?
3. Required level of human approval for inventory or work-management actions?
4. Integration priorities with existing systems (ServiceNow, asset registers, etc.)?

---

## 15. Approval

| Role            | Name             | Date | Signature |
|-----------------|------------------|------|-----------|
| Project Owner   | Danie Ungerer    |      |           |
| Technical Lead  |                  |      |           |

---

*This BRS defines the business intent and scope for the fully open-source Knowledge Management by Slack platform. Implementation details will be refined in subsequent design and technical specification documents.*
