# Superior Incident Knowledge Model

## Acceptance criterion

Frontend Support must be able to:

1. explain an individual incident richly from its actual evidence; and
2. infer repeatable organisational knowledge across many incidents without inventing frequencies or fixes.

## Why the old model was insufficient

The fast temporal importer is valuable evidence infrastructure, but raw vectors and structural graph edges are not reusable support knowledge by themselves. A vector hit can locate an incident; it cannot safely answer what repeatedly fixed a problem across the organisation.

The system therefore separates **evidence intake** from **knowledge enrichment**.

## Architecture

```text
ServiceNow CSV
    |
    v
FAST EVIDENCE INTAKE (no LLM)
    |- incident_current / versions / transitions / dwell (SQLite)
    |- deterministic structural temporal graph
    |- raw BGE-M3 incident vectors
    |
    v
ASYNC KNOWLEDGE ENRICHMENT (local LLM)
    |- canonical issue pattern
    |- user-visible symptom
    |- technology + environment
    |- ordered troubleshooting actions
    |- action outcome: successful / failed / unknown
    |- canonical action label
    |- root cause + canonical root-cause pattern (only when evidenced)
    |- recorded resolution + canonical resolution pattern
    |- resolver + evidence confidence
    |
    +--> support_incident_knowledge (SQLite source of truth)
    +--> support_knowledge_actions (SQLite)
    +--> support_patterns (materialised cross-incident rollups)
    +--> data/vectorstore/support_graph/graph.json
    +--> Chroma collection: support_knowledge
             - symptom locator documents
             - resolution locator documents
             - action locator documents

Frontend Support
    |
    |- exact INC number? -> deterministic exact case file + exact enriched knowledge
    |
    `- symptom/problem? -> enriched symptom vectors locate candidate incidents
                           -> complete enriched records loaded
                           -> deterministic counts across those incidents
                           -> response LLM narrates supplied facts/counts
```

## Key safety rule

Embeddings are **locators**, not the knowledge source.

For a symptom query, BGE-M3 selects symptomatically similar enriched incident IDs. The application then reads their structured enriched records and computes repeat counts in Python/SQLite. The LLM is given those counts; it does not estimate them.

Example evidence packet:

```text
Retrieved enriched incidents: 18
Supporting incidents: INC..., INC..., ...
Observed resolutions:
- Clear cached Office credentials: 11 incidents — INC..., INC..., ...
- Recreate Outlook profile: 3 incidents — INC..., ...
Observed failed actions:
- Reset password only: 7 incidents — INC..., ...
```

Frontend Support may narrate those numbers exactly. It may not turn them into an invented success probability.

## Enriched knowledge schema

### Incident knowledge

Each current incident has at most one active enrichment for its current row hash and extraction model:

- `incident_number`
- `row_hash`
- `model`
- `source_file`
- `pattern_key`
- `pattern_label`
- `symptom`
- `resolution`
- `resolution_pattern`
- `root_cause`
- `root_cause_pattern`
- `technologies`
- `environments`
- `location`
- `assignment_group`
- `confidence`
- complete extraction JSON

A changed ServiceNow snapshot changes the row hash and automatically makes the incident pending for re-enrichment.

### Actions

Actions are ordered and retain both the evidence-specific wording and a canonical reusable label:

- action text
- canonical action
- outcome (`successful`, `failed`, `unknown`)
- contributor when evidenced
- confidence

### Organisational patterns

`support_patterns` materialises rollups by canonical issue pattern with provenance:

- number of enriched incidents
- number with resolution knowledge
- repeated resolution patterns
- repeated successful actions
- repeated failed actions
- root-cause patterns
- technologies
- environments
- locations
- supporting incident numbers
- average extraction confidence

The query-time pattern service also aggregates the symptomatically closest enriched incidents directly, so useful repeat evidence does not depend solely on exact canonical pattern wording.

## Models

- Technician-facing response model: `LLM_MODEL`
- Knowledge extraction model: `SUPPORT_EXTRACTION_MODEL`
- Enriched semantic locator: the existing BGE-M3 incident embedding model

The default extraction model is deliberately the same stronger Qwen3 model used for technician responses. Enrichment is asynchronous; quality is prioritised over CSV upload latency.

## Controlled GX10 validation

Do not start the full backfill before validating a real incident.

```bash
cd ~/knowledge_management_by_slack
source .venv/bin/activate

python scripts/enrich_incident_knowledge.py --status
python scripts/enrich_incident_knowledge.py --incident INC0092846
```

The second command prints:

1. the structured extraction JSON;
2. enriched-vector index result;
3. pattern count; and
4. the exact enriched context Frontend Support will receive.

After the single incident is acceptable, test a small batch:

```bash
python scripts/enrich_incident_knowledge.py --batch 50
python scripts/enrich_incident_knowledge.py --status
```

Then test Frontend Support with both an exact lookup and a natural symptom query.

## Continuous backfill

Run directly while validating:

```bash
python -m src.worker.knowledge_enrichment_worker
```

The worker continuously selects incidents whose current `row_hash` has not been enriched with the configured extraction model. Existing imported incidents therefore form the initial backfill queue automatically; no CSV re-upload is required.

A systemd unit is provided at:

```text
deploy/systemd/knowledge-enrichment-worker.service
```

## Expected Frontend Support behaviour

### Exact incident

Question:

```text
What happened with INC0092846 and how was it resolved?
```

Retrieval must bypass semantic similarity and provide the exact case evidence plus exact enrichment. The response should lead with the recorded outcome, then explain symptom, troubleshooting sequence, failed/successful actions, root cause when supported, resolution and lifecycle/ownership context.

### Repeat organisational knowledge

Question:

```text
User's Outlook keeps asking for their password after the password was reset.
```

Frontend Support should use enriched symptom retrieval and deterministic rollups to explain what matching incidents repeatedly show, what resolutions recur, which actions repeatedly failed, and which incident IDs support those claims. Generic advice is secondary to organisational evidence.

## Non-goals / guardrails

- A single historical incident does not prove a general solution.
- Generic closure notes are not technical resolutions.
- Root cause is never inferred merely because a fix worked.
- Similarity scores are not success probabilities.
- The response LLM cannot manufacture counts.
- Raw evidence remains available for audit and disagreement with enrichment.
