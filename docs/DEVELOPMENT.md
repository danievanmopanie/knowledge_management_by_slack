# Development Guide

## Project Structure

```text
knowledge_management_by_slack/
├── docs/
│   ├── BRS.md                 # Business Requirements Specification
│   └── DEVELOPMENT.md         # This file
├── src/
│   ├── bot/                   # Slack Bolt application & routing
│   ├── agents/                # Specialised agents per channel
│   │   ├── frontend_support/
│   │   ├── inventory/
│   │   ├── work_management/
│   │   └── knowledge_ingest/
│   ├── knowledge/             # RAG / vector store
│   ├── llm/                   # Local LLM client
│   └── core/                  # Config & shared utilities
├── data/
│   ├── raw/                   # Original uploaded documents
│   └── vectorstore/           # Persistent Chroma data
├── main.py                    # Entrypoint
├── pyproject.toml
└── .env.example
```

## Quick Start (local)

1. Copy `.env.example` → `.env` and fill in Slack tokens + model settings.
2. Create a virtual environment and install dependencies:
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -e .
   ```
3. Ensure your local LLM (Ollama / vLLM) is running on the GX10.
4. Start the bot:
   ```bash
   python main.py
   ```

## Next Implementation Priorities

1. Wire channel-based routing in `src/bot/app.py`
2. Implement RAG retrieval in Frontend Support agent
3. Implement file download + ingest pipeline for Knowledge Ingest agent
4. Add basic LangGraph graphs for each agent
5. Add human-in-the-loop confirmation patterns
