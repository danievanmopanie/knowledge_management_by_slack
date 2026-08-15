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

    # Support knowledge extraction. Quality matters more than raw ingest speed:
    # this runs asynchronously after the deterministic evidence intake.
    support_extraction_model: str = "qwen3:30b-a3b"
    support_extraction_temperature: float = 0.0
    support_extraction_concurrency: int = 2
    support_extraction_max_chars: int = 12000
    knowledge_enrichment_batch_size: int = 12
    knowledge_enrichment_poll_seconds: float = 5.0
    knowledge_min_extraction_confidence: float = 0.60
    knowledge_min_similarity: float = 0.42
    knowledge_pattern_candidate_k: int = 40
    knowledge_pattern_max_incidents: int = 24

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

    # Embeddings – incidents and enriched support knowledge
    incident_embedding_model: str = "bge-m3"
    incident_embedding_document_prefix: str = ""
    incident_embedding_query_prefix: str = ""
    incident_embedding_batch_size: int = 64

    # Retrieval quality
    retrieval_min_confidence: float = 0.30
    retrieval_strong_confidence: float = 0.55
    retrieval_max_chunks_per_document: int = 2

    # Paths
    vectorstore_path: Path = Path("./data/vectorstore")
    raw_docs_path: Path = Path("./data/raw")
    staging_path: Path = Path("./data/staging")
    backup_root: Path = Path("./data/backups")
    incidents_path: Path = Path("./data/incidents")
    platform_db_path: Path = Path("./data/platform.db")

    # Upload / ingest policy
    max_upload_bytes: int = 25 * 1024 * 1024
    create_knowledge_incident_upload_bytes: int = 100 * 1024 * 1024
    create_knowledge_download_timeout_seconds: float = 180.0
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

    # Builder Agent (Aider-driven autonomous coding tasks)
    builder_agent_allowed_user_ids: str = ""
    builder_repo_path: Path = Path("./data/builder/repo")
    builder_workdir: Path = Path("./data/builder/worktrees")
    builder_aider_model: str = "ollama_chat/qwen3-coder:30b"
    builder_task_timeout_seconds: int = 1800
    builder_poll_interval_seconds: int = 15
    builder_git_remote: str = "origin"
    builder_base_branch: str = "main"

    # GitHub (pull request creation for Builder Agent)
    github_token: str = ""
    github_api_base_url: str = "https://api.github.com"
    github_repo_owner: str = ""
    github_repo_name: str = ""


settings = Settings()
