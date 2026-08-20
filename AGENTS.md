# AGENTS.md

## Cursor Cloud specific instructions

This repo is a Python 3.11+ multi-agent Slack knowledge-management system (Slack Bolt Socket Mode
+ LangGraph/LangChain + ChromaDB + a local OpenAI-compatible LLM). Standard install/lint/test
commands live in `.github/workflows/ci.yml` and `docs/DEVELOPMENT.md`; the notes below only cover
non-obvious, durable setup/run caveats for this cloud environment.

### Python environment
- Dependencies are installed into a virtualenv at `/workspace/.venv` (the update script keeps it
  fresh). Activate it before running anything: `source .venv/bin/activate`. Editable installs mean
  source edits are picked up without reinstalling.
- Python here is 3.12 (project requires `>=3.11`). CI pins 3.11; both work.
- Two system packages are baked into the VM snapshot and are NOT reinstalled by the update script:
  `python3.12-venv` (needed to create the venv) and `libzbar0` (needed only for the barcode feature
  in `src/inventory/barcode.py`; it imports lazily and degrades gracefully if missing).

### Lint & test (fully offline, no external services)
- Lint: `ruff check src tests scripts`
- Tests: `pytest` (330 tests). The suite mocks Slack/LLM/Chroma/GitHub; `tests/conftest.py` sets
  placeholder `SLACK_*` env vars at import time because `src/core/config.py` instantiates
  `Settings()` eagerly with required Slack fields.

### Running the app / core functionality
- The product needs two external things to run live: (1) real Slack Socket Mode tokens and (2) an
  OpenAI-compatible LLM + embeddings endpoint. There is NO local web UI — it is a Slack bot, so
  demonstrate/verify via the terminal, not a browser.
- Local LLM: Ollama is installed and models `llama3.2:1b` (chat) and `nomic-embed-text` (embeddings)
  are pulled. Ollama is NOT a system service here (systemd is absent), so start it manually when
  needed: `ollama serve` (listens on `127.0.0.1:11434`, OpenAI-compatible under `/v1`). It is safe
  to run in a tmux session.
- A local `.env` (gitignored) points `LLM_*`/`EMBEDDING_*` at Ollama with the small models and sets
  `INCIDENT_EMBEDDING_MODEL=nomic-embed-text` (the default `bge-m3` is not pulled) and
  `VOICE_NOTES_ENABLED=false` (no GPU). Slack tokens in it are format-valid placeholders: they pass
  `validate_slack_readiness` so `python main.py` boots and logs channels, but the Socket Mode
  connection then fails with `invalid_auth`. Provide real Slack tokens to run the live bot.
- To exercise the core RAG pipeline without Slack, drive it directly: ingest a document via
  `src.knowledge.vectorstore.VectorStore.add_documents`, retrieve with
  `src.knowledge.retriever.HybridRetriever`, and generate a grounded, cited answer with
  `src.agents.frontend_support.agent.FrontendSupportAgent.handle(...)` (async). Agents are also
  reachable through `src.bot.router.route_message` given a `RequestContext` whose `channel_id`
  matches a configured `CHANNEL_*`.
- The Builder agent (`#builder`) additionally expects a separate "Atlas/AEON" GPU LLM on
  `127.0.0.1:8888` (see `scripts/run_atlas_builder.sh`); this is optional and not needed for the
  core agents or tests.
