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

    # Local LLM (OpenAI-compatible)
    llm_base_url: str = "http://localhost:11434/v1"
    llm_api_key: str = "ollama"
    llm_model: str = "qwen3:30b-a3b"

    # Embeddings
    embedding_base_url: str = "http://localhost:11434/v1"
    embedding_model: str = "nomic-embed-text"

    # Paths
    vectorstore_path: Path = Path("./data/vectorstore")
    raw_docs_path: Path = Path("./data/raw")
    backup_root: Path = Path("./data/backups")

    # Backup policy
    backup_include_raw: bool = True
    backup_retention_count: int = 14  # keep last N backups
    backup_label_prefix: str = "scheduled"

    # App
    log_level: str = "INFO"
    environment: str = "development"

    # Channel IDs (fill after creating channels in Slack)
    channel_frontend_support: str | None = None
    channel_inventory: str | None = None
    channel_work_management: str | None = None
    channel_knowledge_uploads: str | None = None


settings = Settings()
