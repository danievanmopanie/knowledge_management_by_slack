# Incident RAG

Incident-specific retrieval so technicians get **similar past tickets** as context, not only general knowledge articles.

## What it does

1. Indexes incident records into a dedicated Chroma collection (`incidents`)
2. Links entities in the knowledge graph (assignment groups, callers, locations, categories)
3. At question time, retrieves the most similar past incidents for the Frontend Support agent

## How incidents get indexed

### Automatic (recommended)
Upload a ServiceNow-style CSV to `#knowledge-uploads`. If it looks like an incident export, the bot will:

- Copy it to `data/incidents/`
- Index each ticket into incident RAG
- Also add a text form to the general knowledge base

### Manual reindex

```bash
# Drop CSVs into data/incidents/ then:
python scripts/reindex_incidents.py
```

## Where it is used

- **`#frontend-support` agent** – every question retrieves similar past incidents + knowledge articles
- **Daily / weekly reports** – still use structured CSV stats; incident RAG improves live Q&A

## Expected CSV fields

Flexible matching for common ITSM columns, including:

`number`, `short_description`, `description`, `state`, `assignment_group`, `assigned_to`, `caller`, `location`, `opened_at`, `resolved_at`, `category`, `subcategory`

## Architecture note

| Store | Collection / file | Content |
|-------|-------------------|---------|
| Chroma | `knowledge` | Runbooks, notes, general docs |
| Chroma | `incidents` | One document per incident |
| Graph | `graph.json` | Entities + relationships |
