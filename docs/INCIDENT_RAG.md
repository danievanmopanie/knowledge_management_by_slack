# Incident RAG

Incident-specific retrieval so technicians get **similar past tickets** as context, not only general knowledge articles.

## What it does

1. Indexes incident records into a dedicated Chroma collection (`incidents`)
2. Uses a **dedicated embedding model** optimised for short technical problem text
3. Links entities in the knowledge graph (assignment groups, callers, locations, categories)
4. At question time, retrieves the most similar past incidents for the Frontend Support agent

## Embedding model optimisation

General knowledge and incidents use **separate embedders**:

| Purpose | Config | Default | Why |
|---------|--------|---------|-----|
| Runbooks / articles | `EMBEDDING_MODEL` | `nomic-embed-text` | Fast, long context, good general docs |
| Incidents | `INCIDENT_EMBEDDING_MODEL` | `qwen3-embedding:0.6b` | Stronger short-text / technical retrieval |

### Recommended local models on the GX10

```bash
# Default (balanced)
ollama pull qwen3-embedding:0.6b

# Higher quality (still easy on GX10)
ollama pull qwen3-embedding:4b

# Excellent technical + multilingual alternative
ollama pull bge-m3

# Strong English short-text alternative
ollama pull mxbai-embed-large
```

Set in `.env`:

```env
INCIDENT_EMBEDDING_MODEL=qwen3-embedding:0.6b
```

After changing the incident embedding model, **reindex**:

```bash
python scripts/reindex_incidents.py
```

### How incident text is embedded

Tickets are formatted so the model focuses on the *problem*, not IDs:

1. Short description + description (symptoms / failure)
2. Category / subcategory / location / assignment group
3. Incident number and state last

Long work notes are capped so noise does not dominate the vector.

Optional asymmetric prefixes are supported if your model benefits from them:

```env
INCIDENT_EMBEDDING_DOCUMENT_PREFIX=search_document: 
INCIDENT_EMBEDDING_QUERY_PREFIX=search_query: 
```

## How incidents get indexed

### Automatic
Upload a ServiceNow-style CSV to `#knowledge-uploads`. Incident exports are detected, copied to `data/incidents/`, and indexed with the incident embedder.

### Manual reindex

```bash
python scripts/reindex_incidents.py
```

## Where it is used

- **`#frontend-support` agent** – similar past incidents + knowledge articles
- **Daily / weekly reports** – structured stats from CSVs (RAG improves live Q&A)

## Architecture

| Store | Collection | Embedder |
|-------|------------|----------|
| Chroma | `knowledge` | `EMBEDDING_MODEL` |
| Chroma | `incidents` | `INCIDENT_EMBEDDING_MODEL` |
| Graph | `graph.json` | n/a (entities/relations) |
