"""Application configuration loaded from environment variables."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Slack
    slack_bot_token: str
    slack_app_token: str
    slack_signing_secret: str
    inventory_interactive_allowed_user_ids: str = ""

    # Response model – intelligent answers (Qwen3 on GX10)
    llm_base_url: str = "http://localhost:11434/v1"
    llm_api_key: str = "ollama"
    llm_model: str = "qwen3:30b-a3b"

    # Lightweight support extraction model – batch graph construction
    support_extraction_model: str = "qwen3:4b"
    support_extraction_temperature: float = 0.0
    support_extraction_concurrency: int = 2
    support_extraction_max_chars: int = 12000

    # Local voice-note transcription for field technicians
    voice_notes_enabled: bool = True
    voice_transcription_model: str = "small"
    voice_transcription_device: str = "cuda"
    voice_transcription_compute_type: str = "float16"

    # Embeddings – general knowledge / runbooks
    embedding_base_url: str = "http://localhost:11434/v1"
    embedding_model: str = "nomic-embed-text"
    embedding_document_prefix: str = ""
    embedding_query_prefix: str = ""

    # Embeddings – incidents
    incident_embedding_model: str = "bge-m3"
    incident_embedding_document_prefix: str = ""
    incident_embedding_query_prefix: str = ""
    incident_embedding_batch_size: int = 64

    # Retrieval quality
    retrieval_min_confidence: float = 0.30
    retrieval_strong_confidence: float = 0.55
    retrieval_max_chunks_per_document: int = 2

    # A confirmed fix older than this is still shown as evidence, but flagged for
    # re-verification instead of being presented as current fact.
    support_graph_stale_fix_days: int = 270

    # Paths
    vectorstore_path: Path = Path("./data/vectorstore")
    raw_docs_path: Path = Path("./data/raw")
    staging_path: Path = Path("./data/staging")
    backup_root: Path = Path("./data/backups")
    incidents_path: Path = Path("./data/incidents")
    platform_db_path: Path = Path("./data/platform.db")

    # Upload / ingest policy
    max_upload_bytes: int = 25 * 1024 * 1024
    staging_retention_hours: int = 24

    # Backup policy
    backup_include_raw: bool = True
    backup_retention_count: int = 14
    backup_label_prefix: str = "scheduled"

    # Reporting
    report_daily_enabled: bool = True
    report_afternoon_enabled: bool = True
    report_weekly_enabled: bool = True
    report_weekly_location: str | None = None
    report_daily_hours: int = 24
    report_afternoon_hours: int = 10
    report_aging_days: int = 3
    report_stale_hours: int = 24

    # Project-wide background activity UX
    # Long-running user-triggered work must remain visibly alive. Runtimes should
    # update one durable status surface in place at this cadence while a phase
    # is active, without exposing internal reasoning or noisy logs.
    background_heartbeat_seconds: int = 30

    # App
    log_level: str = "INFO"
    environment: str = "development"

    # Channel IDs
    channel_frontend_support: str | None = None
    channel_inventory: str | None = None
    channel_work_management: str | None = None
    channel_knowledge_uploads: str | None = None
    channel_create_knowledge: str | None = None
    channel_builder_agent: str | None = None

    # Builder Agent – natural on-device engineering harness
    builder_agent_allowed_user_ids: str = ""
    builder_repo_path: Path = Path("./data/builder/repo")
    builder_workdir: Path = Path("./data/builder/worktrees")

    # Keep Builder inference independent from the rest of the Slack agents so
    # Atlas can be introduced as a canary without moving support/extraction.
    builder_aider_model: str = "openai/aeon-builder"
    builder_llm_model: str = "aeon-builder"
    builder_llm_base_url: str = "http://127.0.0.1:8888/v1"
    builder_llm_api_key: str = "atlas-local"
    builder_model_checkpoint: str = "AEON-7/Qwen3.8-27B-AEON-ULTIMATE-UNCENSORED-BF16"

    # Real terminal/tool loop. Commands execute on the GX10 as the Builder
    # service account with a scrubbed environment; no sudo bypass is provided.
    builder_terminal_enabled: bool = True
    builder_terminal_max_steps: int = 16
    builder_terminal_command_timeout_seconds: int = 180
    builder_terminal_max_command_timeout_seconds: int = 900
    builder_terminal_output_chars: int = 12000
    builder_terminal_max_tokens: int = 4096
    builder_terminal_temperature: float = 0.1
    builder_terminal_allow_docker_run: bool = False

    # Local code validation gate. {python} resolves to the worker's interpreter.
    # A PR is never pushed when this command fails when require_tests_pass=True.
    builder_test_command: str = (
        "{python} -m ruff check src tests scripts && {python} -m pytest -q"
    )
    builder_test_timeout_seconds: int = 1200
    builder_max_repair_attempts: int = 2
    builder_require_tests_pass: bool = True
    builder_validation_output_chars: int = 8000

    builder_task_timeout_seconds: int = 1800
    builder_poll_interval_seconds: int = 15
    builder_git_remote: str = "origin"
    builder_base_branch: str = "main"

    # GitHub (pull request creation / handoff for Builder Agent)
    github_token: str = ""
    github_api_base_url: str = "https://api.github.com"
    github_repo_owner: str = ""
    github_repo_name: str = ""


settings = Settings()
