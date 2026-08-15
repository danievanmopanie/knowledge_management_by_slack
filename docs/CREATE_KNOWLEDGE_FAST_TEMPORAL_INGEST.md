# Create Knowledge — fast temporal ServiceNow incident ingest

This slice ports the highest-value low-cost features from `itsm-temporal-graph-rag` into the Slack Knowledge Management platform.

## Design goal

Speed is the primary constraint. The ingestion hot path performs **no LLM extraction**.

A ServiceNow Incident CSV is processed as:

1. Parse and profile the snapshot.
2. Compare against SQLite `incident_current`.
3. Persist only new/changed versions.
4. Emit deterministic field transitions.
5. Calculate assignment-group dwell when a group is left or the incident resolves.
6. Flag previously known open incidents missing from a complete later snapshot.
7. In parallel:
   - update deterministic temporal graph relationships;
   - embed only incidents whose semantic searchable content changed.
8. Lifecycle-only changes update vector metadata without recomputing embeddings.

## Temporal data retained

SQLite tables in `PLATFORM_DB_PATH`:

- `incident_current`
- `incident_versions`
- `incident_transitions`
- `assignment_dwell`
- `incident_ingest_runs`
- `incident_ingest_jobs`

The lightweight NetworkX graph keeps temporal relationship properties:

- `valid_from`
- `valid_to`
- `observed_at`
- `closed_observed_at`

Structured relationships include state, priority, assignment group, assignee, location, category, subcategory and configuration item.

Ticket references are extracted deterministically. Change references are confidence graded rather than automatically asserted as causal:

- `mentions_change` — 0.25
- `temporally_correlated_with` — 0.55
- `likely_caused` — 0.70
- `confirmed_caused` — 0.90

## Vector representation

At most three focused documents per incident:

- problem: short description + description
- troubleshooting: work notes + comments
- resolution: resolution notes

Volatile state/assignment facts live in metadata and the temporal graph, not the embedded text. This means a reassignment can be captured without spending GPU time on a new embedding.

## GX10 isolated 1,000-incident benchmark

The isolated mode uses its own temporary:

- Chroma vector directory
- SQLite temporal database
- NetworkX graph JSON
- incident semantic-hash file

It therefore does **not** read or modify the configured/live incident knowledge state.

Generate the deterministic synthetic Day-2 snapshot:

```bash
python scripts/generate_synthetic_day2_incidents.py
```

By default this reads:

```text
~/Downloads/closed_incidents_sample_1000.csv
```

and creates:

```text
~/Downloads/closed_incidents_sample_1000_day2.csv
~/Downloads/closed_incidents_sample_1000_day2.manifest.json
```

The default Day-2 plan changes exactly 100 unique incident numbers:

- 40 lifecycle-only changes (Assignment Group / Assigned To / State, depending on available columns)
- 30 Work Notes changes
- 30 Resolution Notes changes

The generator uses a fixed seed (`20260815`) and records the exact selected incident numbers in the manifest. Every duplicate CSV row for a selected incident is changed consistently.

Run the full clean experiment:

```bash
python scripts/run_gx10_1000_incident_benchmark.py --isolated --with-day2
```

This performs, in the same temporary isolated state:

1. Day-1 initial load — clean vector/graph build.
2. Day-1 identical repeat — unchanged fast path.
3. Synthetic Day-2 incremental load — mixed temporal and semantic changes.

Expected Day-2 shape:

```text
changed                         100
unchanged                       ~797
vector_metadata_only_incidents   ~40
vector_incidents                 ~60
```

The exact vector-document count depends on which focused field groups exist for the 60 semantically changed incidents.

To preserve the isolated state for inspection instead of deleting it at process exit:

```bash
python scripts/run_gx10_1000_incident_benchmark.py \
  --isolated-dir ./data/benchmark_1000 \
  --with-day2
```

## Generic CLI benchmark

Profile only:

```bash
python scripts/benchmark_temporal_incident_ingest.py /path/to/incidents.csv --profile-only
```

Full snapshot ingest:

```bash
python scripts/benchmark_temporal_incident_ingest.py /path/to/incidents.csv
```

For a sample/subset that must **not** mark omitted incidents as missing:

```bash
python scripts/benchmark_temporal_incident_ingest.py /path/to/incidents_1000_sample.csv --partial-snapshot
```

## Slack flow

Create Knowledge is a standalone Slack app using:

- `CREATE_KNOWLEDGE_SLACK_BOT_TOKEN`
- `CREATE_KNOWLEDGE_SLACK_APP_TOKEN`
- `CHANNEL_CREATE_KNOWLEDGE`

Manifest: `slack_apps/create_knowledge/manifest.json`

Runtime:

```bash
python -m src.bot.create_knowledge_app
```

Worker:

```bash
python -m src.worker.incident_ingest_worker
```

Upload a ServiceNow incident CSV into `#create-knowledge`. The app profiles it and returns a stage ID. Confirm with:

```text
confirm <stage-id>
```

The confirmation queues a durable `ING-...` job immediately. Check it with:

```text
status ING-...
```

The background worker posts start/completion messages and detailed throughput metrics.

## systemd

Units:

- `deploy/systemd/create-knowledge-agent.service`
- `deploy/systemd/incident-ingest-worker.service`

Do not enable the Create Knowledge app service until its dedicated Slack credentials are present in `.env`.

## Intentionally deferred

The following are useful but are not in the ingestion hot path yet:

- LLM-generated Symptom / Action / Resolution entities
- Graphiti
- Neo4j
- model-based root-cause classification
- semantic clustering / repeat-theme discovery

Those can run asynchronously later against already-ingested incidents without slowing daily snapshot availability.
