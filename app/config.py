"""Application settings loaded from environment / .env."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="FINEYE_",
        extra="ignore",
    )

    data_dir: Path = Field(default=Path("data"))
    wal_dir: Path = Field(default=Path(".wal"))
    tmp_dir: Path = Field(default=Path(".tmp"))
    default_timezone: str = "Asia/Kolkata"
    default_currency: str = "INR"
    log_level: str = "INFO"

    # Groq LLM — read from env without FINEYE_ prefix (see .env.example).
    GROQ_API_KEY: str = Field(default="", validation_alias="GROQ_API_KEY")
    GROQ_MODEL: str = Field(
        default="llama-3.3-70b-versatile", validation_alias="GROQ_MODEL"
    )
    GROQ_BASE_URL: str = Field(
        default="https://api.groq.com/openai/v1", validation_alias="GROQ_BASE_URL"
    )

    # Google Sheets backup (M6) — read from env without FINEYE_ prefix.
    GOOGLE_SHEETS_CREDENTIALS_PATH: str = Field(
        default="", validation_alias="GOOGLE_SHEETS_CREDENTIALS_PATH"
    )
    GOOGLE_SHEETS_SPREADSHEET_ID: str = Field(
        default="", validation_alias="GOOGLE_SHEETS_SPREADSHEET_ID"
    )
    GOOGLE_SHEETS_ENABLED: bool = Field(
        default=False, validation_alias="GOOGLE_SHEETS_ENABLED"
    )
    GOOGLE_SHEETS_MAX_RETRIES: int = Field(
        default=6, validation_alias="GOOGLE_SHEETS_MAX_RETRIES"
    )
    GOOGLE_SHEETS_BACKOFF_BASE: float = Field(
        default=1.0, validation_alias="GOOGLE_SHEETS_BACKOFF_BASE"
    )

    # Supabase / Postgres
    DB_HOST: str = Field(default="", validation_alias="DB_HOST")
    DB_PORT: int = Field(default=5432, validation_alias="DB_PORT")
    DB_USER: str = Field(default="postgres", validation_alias="DB_USER")
    DB_PASSWORD: str = Field(default="", validation_alias="DB_PASSWORD")
    DB_NAME: str = Field(default="postgres", validation_alias="DB_NAME")

    # Personal owner UUID — all rows are tagged with this for future multi-user RLS
    OWNER_ID: str = Field(default="", validation_alias="FINEYE_OWNER_ID")

    # "csv" (local, dual-writes to Supabase) | "supabase" (Vercel, Supabase primary)
    STORAGE_BACKEND: str = Field(default="csv", validation_alias="FINEYE_STORAGE_BACKEND")

    # Simple password protecting the app on Vercel (empty = no protection)
    APP_PASSWORD: str = Field(default="", validation_alias="FINEYE_APP_PASSWORD")

    @property
    def supabase_dsn(self) -> str:
        return (
            f"host={self.DB_HOST} port={self.DB_PORT} "
            f"dbname={self.DB_NAME} user={self.DB_USER} "
            f"password={self.DB_PASSWORD} sslmode=require"
        )

    @property
    def supabase_configured(self) -> bool:
        return bool(self.DB_HOST and self.DB_PASSWORD)

    def resolved_data_dir(self) -> Path:
        return self.data_dir.resolve()

    def resolved_wal_dir(self) -> Path:
        return self.wal_dir.resolve()

    def resolved_tmp_dir(self) -> Path:
        return self.tmp_dir.resolve()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
