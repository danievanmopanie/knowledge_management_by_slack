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

    # Response model – intelligent answers (Qwen3 on GX10)
    llm_base_url: str = "http://localhost:11434/v1"
    llm_api_key: str = "ollama"
    llm_model: str = "qwen3:30b-a3b"

    # Embeddings – general knowledge / runbooks (lightweight)
    embedding_base_url: str = "http://localhost:11434/v1"
    embedding_model: str = "nomic-embed-text"
    embedding_document_prefix: str = ""
    embedding_query_prefix: str = ""

    # Embeddings – incidents (lightweight BGE; good quality on technical text)
    # Pull with: ollama pull bge-m3
    incident_embedding_model: str = "bge-m3"
    incident_embedding_document_prefix: str = ""
    incident_embedding_query_prefix: str = ""
    # Batch size when indexing large incident CSVs
    incident_embedding_batch_size: int = 64

    # Paths
    vectorstore_path: Path = Path("./data/vectorstore")
    raw_docs_path: Path = Path("./data/raw")
    backup_root: Path = Path("./data/backups")
    incidents_path: Path = Path("./data/incidents")

    # Backup policy
    backup_include_raw: bool = True
    backup_retention_count: int = 14
    backup_label_prefix: str = "scheduled"

    # Reporting
    report_daily_enabled: bool = True
    report_weekly_enabled: bool = True
    report_weekly_location: str | None = None
    report_daily_hours: int = 24

    # Work Management (#work-management)
    work_management_db_path: Path = Path("./data/work_management/workmgmt.db")
    work_approval_sla_hours: int = 24
    planning_session_day: str = "Friday"
    planning_session_cutoff_hour: int = 17
    daily_execution_cutoff_hour: int = 12
    routine_keeper_enabled: bool = True

    # App
    log_level: str = "INFO"
    environment: str = "development"

    # Channel IDs
    channel_frontend_support: str | None = None
    channel_inventory: str | None = None
    channel_work_management: str | None = None
    channel_knowledge_uploads: str | None = None


settings = Settings()
