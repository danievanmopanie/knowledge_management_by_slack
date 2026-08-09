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
- What successful fix is most strongly associated with this symptom?
- Is this becoming a repeat incident pattern?
- Who should be invited into the thread because they have relevant experience?

## Current implementation

The branch introduces:

- `src/knowledge/support_graph.py` - typed graph entities and relationships
- `src/knowledge/support_ingest.py` - deterministic graph seeding from incident CSV fields
- `src/knowledge/support_evidence.py` - one evidence package combining governed knowledge, incident vectors and graph context
- `scripts/rebuild_support_graph.py` - rebuild the typed graph from historical CSV incident exports
- `FrontendSupportAgent` now consumes the combined three-layer evidence package

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

## Slack collaboration behaviour to build next

1. Listen to all `#frontend-support` conversation without treating every root message as a case.
2. Classify messages as social/collaborative, technical problem, troubleshooting contribution, question or likely resolution.
3. For technical context, retrieve the three-layer evidence package.
4. Proactively speak only when there is high-value evidence, a repeat-incident pattern, a direct question, a meaningful state change or likely resolution.
5. Track contributor identity on troubleshooting actions.
6. When resolution language is detected, ask the requester for the incident number if it is not already known.
7. Ask the original requester or identified resolver: `It looks like this issue is resolved. Capture this as reusable knowledge?`
8. On confirmation, create a proposed KnowledgeItem from symptom, environment, attempted actions, root cause, successful fix, contributors and source thread.

## Storage evolution

The typed graph currently sits on the existing `GraphStore` / NetworkX persistence so the architecture can be introduced without a disruptive database migration. The domain boundary is intentionally isolated in `SupportKnowledgeGraph`, allowing the backing store to move to Neo4j later without changing the Slack agent or extraction schema.
