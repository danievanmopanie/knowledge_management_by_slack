# Incident RAG

Incident-specific retrieval so technicians get **similar past tickets** as context.

## Models

| Role | Default | Notes |
|------|---------|-------|
| Embeddings (incidents) | `bge-m3` | Lightweight, good on technical text |
| Embeddings (general) | `nomic-embed-text` | Runbooks / articles |
| Responses | `qwen3:30b-a3b` | Intelligent answers |

```bash
ollama pull bge-m3
ollama pull qwen3:30b-a3b
```

## Free-text columns embedded

- Short Description
- Description
- Work Notes
- Comments
- Resolution Notes

Plus category, location, assignment group for context.

## Daily CSV uploads – content-hash dedupe

Large daily ServiceNow exports often repeat unchanged rows. On each index we:

1. Hash key fields (free text + state, group, category, location, …)
2. Compare to `data/incident_content_hashes.json`
3. **Only embed new or changed rows**
4. Skip identical rows (no re-embedding)

Force a full rebuild when needed:

```bash
python scripts/reindex_incidents.py --force
```

Normal incremental run:

```bash
python scripts/reindex_incidents.py
```

## How incidents get indexed

### Automatic
Upload a ServiceNow-style CSV to `#knowledge-uploads`. Incident exports are detected, copied to `data/incidents/`, and indexed with dedupe.

### Manual
Drop CSVs into `data/incidents/` then run the reindex script above.

## Architecture

| Store | Collection / file | Role |
|-------|-------------------|------|
| Chroma | `incidents` | One vector per changed ticket |
| JSON | `data/incident_content_hashes.json` | Skip unchanged daily rows |
| Graph | `graph.json` | Groups, callers, locations |
