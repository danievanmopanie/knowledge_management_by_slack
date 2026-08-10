# Frontend Support Knowledge Pilot Runbook

This runbook is for the first controlled pilot of the collaborative `#frontend-support` knowledge agent on the GX10.

## Pilot objective

Prove four things on a small real ServiceNow sample before broad rollout:

1. Similar incidents are retrieved for the right reason: problem, troubleshooting, or resolution.
2. The 4B extraction model produces useful structured support facts without inventing root causes or outcomes.
3. Slack collaboration stays quiet during normal team conversation and intervenes only when it can add value.
4. Confirmed human resolutions become reusable, attributable knowledge tied to the ServiceNow incident and Slack thread.

## 1. Verify model configuration

Recommended split:

```text
LLM_MODEL=qwen3:30b-a3b
SUPPORT_EXTRACTION_MODEL=qwen3:4b
SUPPORT_EXTRACTION_CONCURRENCY=2
INCIDENT_EMBEDDING_MODEL=bge-m3
```

The response model is for technician-facing conversation. The smaller model is for batch extraction only. BGE-M3 handles incident similarity.

## 2. Start with a small known incident sample

Use approximately 100–250 resolved incidents that contain a useful mix of:

- meaningful descriptions
- work notes / comments
- resolution notes
- known repeat issues
- several incidents where you already know the correct fix

Do not use the full production corpus for the first validation run.

Place the CSV export in the configured incident data directory (`INCIDENTS_PATH`).

## 3. Build the new field-aware incident index

The embedding schema is versioned. The first run after this change will rebuild historical incidents into separate field documents.

For the controlled pilot, an explicit force rebuild is clearest:

```bash
python scripts/reindex_incidents.py --force
```

Each incident can create up to three vector documents:

- `problem` — Short Description + Description
- `troubleshooting` — Work Notes + Comments
- `resolution` — Resolution Notes

Daily runs after the migration can omit `--force`; unchanged incident snapshots will then be skipped using content hashes.

## 4. Inspect retrieval before involving the LLM

Use the diagnostic query command to validate known examples directly:

```bash
python scripts/query_incident_rag.py "Outlook keeps asking for credentials"
```

Restrict a query to a specific evidence type when useful:

```bash
python scripts/query_incident_rag.py "clear WAM token cache" --field resolution
python scripts/query_incident_rag.py "rebuilt Outlook profile" --field troubleshooting
python scripts/query_incident_rag.py "credential prompts" --field problem
```

For each known test case, check:

- expected incident appears in the top 5
- matched field makes sense
- relevance ordering is sensible
- a resolution-only match is not being confused with a problem-only match

## 5. Run structured extraction on a small batch

Start with 100 resolved incidents:

```bash
python scripts/extract_support_knowledge.py --resolved-only --limit 100
```

Review a sample of the extracted records manually. Focus on:

- symptom accuracy
- troubleshooting actions actually present in the source
- action outcome accuracy
- resolution accuracy
- contributor / resolver attribution
- root cause only when the source genuinely supports it
- confidence scores that reflect evidence quality

Do not treat inferred root cause as trusted merely because the model produced one.

## 6. Pilot in Slack

In the configured `#frontend-support` channel, test several natural scenarios:

### Normal conversation

Examples such as “Thanks”, “Nice one”, or general encouragement should not invoke the support LLM.

### New support discussion

A technical problem should create thread state and prompt the original requester for the ServiceNow incident number if one has not been supplied.

### Collaborative troubleshooting

Multiple technicians should be able to contribute steps in the thread. The agent context should preserve who said what and avoid recommending steps already reported as unsuccessful.

### Resolution

Natural language such as:

```text
That's fixed it.
Working now.
Issue resolved.
```

should trigger the knowledge-capture control rather than another troubleshooting reply.

Only the original requester or identified resolver should be able to confirm the capture.

## 7. Pilot acceptance criteria

Do not expand the pilot until all of the following are true:

- known repeat incidents are usually present in the top 5 retrieval results
- field labels correctly explain why the incident matched
- extracted resolutions are grounded in source text
- unsupported root causes are rare and identifiable by confidence / review
- the bot does not interrupt ordinary team conversation
- technicians can see value in prior fixes without losing attribution to the people who contributed them
- captured Slack resolutions remain linked to their incident and source thread

## 8. Scale-up sequence

After the small pilot passes:

1. Reindex the full incident corpus.
2. Run extraction in bounded batches rather than one unbounded job.
3. Keep daily content-hash dedupe enabled.
4. Monitor retrieval quality using known repeat-incident test queries.
5. Expand Slack participation gradually.
6. Move the support graph from NetworkX to Neo4j only when relationship scale or expertise queries justify the operational complexity.

## Useful commands

```bash
# Force rebuild field-aware incident embeddings
python scripts/reindex_incidents.py --force

# Normal daily reindex; unchanged snapshots are skipped
python scripts/reindex_incidents.py

# Inspect top five incident matches
python scripts/query_incident_rag.py "your symptom or error here"

# Inspect only proven-resolution similarity
python scripts/query_incident_rag.py "known fix" --field resolution

# Extract a small resolved-incident pilot
python scripts/extract_support_knowledge.py --resolved-only --limit 100

# Rebuild deterministic support graph seed data
python scripts/rebuild_support_graph.py
```
