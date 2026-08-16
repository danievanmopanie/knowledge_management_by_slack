# Frontend Support Knowledge Architecture

## Purpose

`#frontend-support` is a collaborative troubleshooting and knowledge-sharing channel. It is not a ticket queue and not every top-level message is a support case. The agent should listen to the conversation, recognise technical support context, compare current symptoms with historical incidents, help technicians collaborate, preserve who contributed what, and promote confirmed resolutions into reusable knowledge.

## Three-layer knowledge architecture

### Layer 1 - Raw and governed evidence

Retain source material and provenance:

- ServiceNow incident CSV exports
- Formal knowledge articles and runbooks
- Confirmed reusable Slack resolutions
- Source incident number and source Slack thread

This layer is the audit trail. The LLM must never invent a source that is not present here.

### Layer 2 - Vector retrieval

Use embeddings for fuzzy similarity and semantic retrieval.

Historical incident free text should be retrievable from:

- Short Description / Description - problem and symptom similarity
- Work Notes / Comments - troubleshooting similarity
- Resolution Notes - successful-fix similarity
- A derived incident summary can be added later for whole-case retrieval

The current implementation continues to use `IncidentRAG` for historical incident vectors and `HybridRetriever` for governed knowledge.

### Layer 3 - Support knowledge graph

Use typed entities and relationships for collective expertise and repeat-resolution reasoning.

#### Core entities

| Entity | Meaning |
|---|---|
| `Incident` | A referencable ServiceNow incident such as `INC0012345` |
| `Person` | Requester, technician, contributor or resolver |
| `Symptom` | Observed problem or behaviour |
| `Action` | Troubleshooting action attempted |
| `Resolution` | Successful fix and optional root cause |
| `Technology` | Application, service, device class or technology domain |
| `Environment` | Location or environment where the issue occurred |
| `SlackThread` | Collaborative Slack discussion linked to an incident |
| `KnowledgeItem` | Human-confirmed reusable knowledge |

#### Core relationships

```text
Incident -HAS_SYMPTOM-> Symptom
Incident -AFFECTS-> Technology
Incident -OCCURRED_IN-> Environment
Incident -TRIED-> Action
Incident -FAILED_ACTION-> Action
Person -CONTRIBUTED-> Action
Person -RESOLVED-> Incident
Incident -SUCCESSFUL_FIX-> Resolution
Resolution -RESOLVED_BY-> Person
Incident -DISCUSSED_IN-> SlackThread
SlackThread -REFERENCES-> Incident
Resolution -PROMOTED_TO-> KnowledgeItem
Incident -SIMILAR_TO-> Incident
```

This makes questions possible that vector search alone handles poorly, for example:

- Who has fixed this type of issue before?
- Which troubleshooting steps repeatedly failed?
- What successful fix is most strongly associated with this symptom, and how recently was it confirmed?
- Is this becoming a repeat incident pattern?
- Who should be invited into the thread because they have relevant experience?

### Temporal layer

Every graph entity and relationship carries time, not just structure:

- Entities track `first_seen_at` / `last_seen_at`.
- Relationships track `occurred_at` (when the underlying fact happened — backdated
  from the incident's `resolved_at`/`opened_at` during CSV seeding, not import time)
  and `created_at` (when the edge was recorded).
- `SupportKnowledgeGraph.related(..., order_by_recency=True)` returns the most
  recently confirmed facts first and annotates each with `age_days`.
- `SupportEvidenceService` merges graph facts across every matched incident and
  globally re-sorts by recency, so the evidence package always leads with the most
  recently confirmed fix rather than whichever incident happened to be indexed first.
- A `successful_fix` older than `STALE_FIX_DAYS` (default 270 days) is labelled
  "not reconfirmed recently" in the evidence text, and the agent's system prompt
  instructs it to say so plainly and suggest re-verifying rather than presenting an
  old fix as current fact.

This is what makes the retrieval genuinely temporal-graph RAG rather than a
timeless graph: two incidents with the same symptom but different-age resolutions
are no longer indistinguishable evidence.

## Current implementation

The branch introduces:

- `src/knowledge/support_graph.py` - typed, temporally-aware graph entities and relationships
- `src/knowledge/support_ingest.py` - deterministic graph seeding from incident CSV fields, backdated to when the incident actually happened
- `src/knowledge/support_evidence.py` - one evidence package combining governed knowledge, incident vectors and recency-ordered graph context
- `src/knowledge/article_drafting.py` - gen-AI drafting of a structured Markdown knowledge article from confirmed field evidence
- `scripts/rebuild_support_graph.py` - rebuild the typed graph from historical CSV incident exports
- `FrontendSupportAgent` now consumes the combined three-layer evidence package and can ask one focused clarifying question when it has some evidence but not enough to answer confidently

The deterministic seed deliberately records only facts directly supported by CSV fields. It does not pretend that unstructured work notes have already been correctly converted into actions or root causes.

## Next extraction layer

A small local extraction model should process changed incidents and confirmed Slack threads into strict structured output:

```json
{
  "incident_number": "INC0012345",
  "symptoms": [
    {"text": "Repeated Outlook credential prompts", "confidence": 0.96}
  ],
  "technology": ["Microsoft Outlook", "Microsoft 365 authentication"],
  "environment": ["Windows 11"],
  "actions": [
    {
      "text": "Rebuild Outlook profile",
      "outcome": "failed",
      "contributor": "Jacob Tech"
    }
  ],
  "resolution": {
    "text": "Clear WAM token cache",
    "root_cause": "Corrupted authentication token",
    "resolver": "Jane Tech",
    "confidence": 0.91
  }
}
```

Only extracted facts above a configured confidence threshold should be promoted into graph relationships automatically. Low-confidence facts should remain evidence for the conversational LLM, not trusted graph truth.

## Slack collaboration behaviour

1. Listen to all `#frontend-support` conversation without treating every root message as a case.
2. Classify messages as social/collaborative, technical problem, troubleshooting contribution, question or likely resolution.
3. For technical context, retrieve the three-layer evidence package.
4. Proactively speak only when there is high-value evidence, a repeat-incident pattern, a direct question, a meaningful state change or likely resolution.
5. When evidence is present but not enough for a confident, specific answer, ask exactly one focused clarifying question instead of guessing or dumping a generic checklist. A reply in that thread routes back to the assistant even if the reply text alone (e.g. a device model) doesn't look technical — see "Natural clarifying follow-up" below.
6. Track contributor identity on troubleshooting actions.
7. When resolution language is detected, ask the requester for the incident number if it is not already known.
8. Ask the original requester or identified resolver: `It looks like this issue is resolved. Capture this as reusable knowledge?`
9. On confirmation, draft a formal knowledge article from the confirmed symptom, attempted actions, resolution, contributors and source thread — see "Create-knowledge flow" below.

### Natural clarifying follow-up

`FrontendSupportAgent`'s system prompt allows exactly one clarifying question per turn,
returned as `CLARIFY: <question>`. The agent strips that prefix, sends only the question
(no evidence footer), and tags the reply with an invisible marker
(`src/agents/frontend_support/agent.py::CLARIFICATION_MARKER`). `src/bot/app.py` detects
the marker and sets `awaiting_clarification` on the thread via
`FrontendThreadStore.set_awaiting_clarification`. The next message in that thread invokes
the agent regardless of how `FrontendCollaborationService.classify()` would otherwise
categorise it, and the flag is single-use — consumed by whatever arrives next.

### Create-knowledge flow

Confirming a resolution no longer hands a reviewer raw symptom/resolution text to
rewrite from scratch. `src/bot/frontend_interactivity.py::_propose_knowledge_task`:

1. Checks governed knowledge for a likely duplicate first. A **strong** match skips
   creating anything — the thread is told which existing article already covers it, and
   the confirmed fix stays recorded as operational evidence only. A **weak** match still
   drafts a new article but flags the related document so a reviewer updates it in place
   instead of publishing a near-duplicate.
2. Always drafts a full structured Markdown article
   (`src/knowledge/article_drafting.py::draft_knowledge_article`) — Symptom, Environment,
   Root cause, Resolution steps, Validation, Related incidents — grounded only in
   confirmed evidence, with an honest "Root cause not confirmed" fallback rather than an
   invented one.
3. Posts one Block Kit card to `#create-knowledge`
   (`src/bot/frontend_actions.py::build_knowledge_task_blocks`) with **Publish new
   article**, **Update existing article instead** (when a related document was flagged),
   **Assign a teammate** (a Slack user picker) and **Dismiss**.
4. Publish/update calls `commit_knowledge()` directly — the drafted article becomes
   searchable governed knowledge immediately, versioned under the same document identity
   when updating an existing article. Assignment DMs the assignee
   (`src/bot/notify.py::send_dm`) with the problem, the confirmed resolution and a link
   back to the review channel.

## Storage evolution

The typed graph currently sits on the existing `GraphStore` / NetworkX persistence so the architecture can be introduced without a disruptive database migration. The domain boundary is intentionally isolated in `SupportKnowledgeGraph`, allowing the backing store to move to Neo4j later without changing the Slack agent or extraction schema.
