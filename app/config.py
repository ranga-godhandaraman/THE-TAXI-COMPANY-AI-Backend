"""Application settings loaded from environment / .env."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Project root (…/UK_taxi_RAG) — where the existing .env lives
PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = PROJECT_ROOT / ".env"
DATASET_DIR = PROJECT_ROOT / "uk_taxi_dataset"


class Settings(BaseSettings):
    """Central configuration for LLM, Neon PostgreSQL, and Qdrant."""

    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Application
    app_name: str = "UK Taxi Operations Assistant"
    app_env: str = Field(default="development", alias="APP_ENV")
    api_prefix: str = "/api"
    cors_origins: str = Field(
        default="http://localhost:5173,http://127.0.0.1:5173",
        alias="CORS_ORIGINS",
    )

    # LLM — Groq only (no OpenAI provider)
    groq_api_key: str | None = Field(default=None, alias="GROQ_API_KEY")
    groq_model: str = Field(default="qwen/qwen3.6-27b", alias="GROQ_MODEL")

    # Neon PostgreSQL (required for structured data layer)
    neon_postgres_string: str | None = Field(default=None, alias="NEON_POSTGRES_STRING")

    # Legacy local Postgres keys (unused when Neon is configured)
    postgres_host: str = Field(default="localhost", alias="POSTGRES_HOST")
    postgres_port: int = Field(default=5432, alias="POSTGRES_PORT")
    postgres_user: str = Field(default="taxi", alias="POSTGRES_USER")
    postgres_password: str | None = Field(default=None, alias="POSTGRES_PASSWORD")
    postgres_db: str = Field(default="uk_taxi", alias="POSTGRES_DB")
    database_url: str | None = Field(default=None, alias="DATABASE_URL")

    # Qdrant (existing .env keys — read-only usage)
    quad_endpoint: str | None = Field(default=None, alias="QUAD_ENDPOINT")
    quad_api_key: str | None = Field(default=None, alias="QUAD_API_KEY")
    qdrant_collection: str = Field(
        default="uk_taxi_policies",
        alias="QDRANT_COLLECTION",
    )
    # Embedding provider: qdrant (Cloud Inference, default) or local (dev opt-in)
    embedding_provider: str = Field(default="qdrant", alias="EMBEDDING_PROVIDER")
    # Qdrant Cloud Inference model id — must match collection (1024-dim bge-m3)
    qdrant_inference_model: str = Field(
        default="bge-m3",
        alias="QDRANT_INFERENCE_MODEL",
    )
    # Local SentenceTransformer name — only when EMBEDDING_PROVIDER=local
    rag_embedding_model: str = Field(
        default="BAAI/bge-m3",
        alias="RAG_EMBEDDING_MODEL",
    )

    # Pool
    db_pool_size: int = Field(default=5, alias="DB_POOL_SIZE")
    db_max_overflow: int = Field(default=10, alias="DB_MAX_OVERFLOW")
    db_pool_timeout: int = Field(default=30, alias="DB_POOL_TIMEOUT")

    # Auth sessions
    auth_cookie_name: str = Field(default="ttc_session", alias="AUTH_COOKIE_NAME")
    auth_session_days: int = Field(default=7, alias="AUTH_SESSION_DAYS")
    auth_cookie_secure: bool | None = Field(default=None, alias="AUTH_COOKIE_SECURE")

    @field_validator(
        "neon_postgres_string",
        "groq_api_key",
        mode="before",
    )
    @classmethod
    def _blank_to_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def neon_dsn(self) -> str:
        if not self.neon_postgres_string:
            raise RuntimeError(
                "NEON_POSTGRES_STRING is missing. "
                "Set it in the project-root .env file."
            )
        return self.neon_postgres_string.strip()

    @property
    def async_database_url(self) -> str:
        """SQLAlchemy async URL derived from NEON_POSTGRES_STRING."""
        return to_asyncpg_url(self.neon_dsn)

    @property
    def sync_database_url(self) -> str:
        """psycopg2-compatible URL derived from NEON_POSTGRES_STRING."""
        return to_psycopg_url(self.neon_dsn)

    @property
    def qdrant_configured(self) -> bool:
        return bool(self.quad_endpoint and self.quad_api_key)

    @property
    def postgres_configured(self) -> bool:
        return bool(self.neon_postgres_string)

    @property
    def llm_configured(self) -> bool:
        return bool(self.groq_api_key)

    @property
    def is_production(self) -> bool:
        return self.app_env.strip().lower() in {"production", "prod"}

    @property
    def cookie_secure(self) -> bool:
        if self.auth_cookie_secure is not None:
            return self.auth_cookie_secure
        return self.is_production

    @property
    def dataset_dir(self) -> Path:
        return DATASET_DIR


def to_asyncpg_url(dsn: str) -> str:
    """Convert a Neon postgresql:// DSN to postgresql+asyncpg://."""
    url = dsn.strip()
    if url.startswith("postgresql+asyncpg://"):
        parsed = urlparse(url)
    elif url.startswith("postgresql://"):
        parsed = urlparse("postgresql+asyncpg://" + url[len("postgresql://") :])
    elif url.startswith("postgres://"):
        parsed = urlparse("postgresql+asyncpg://" + url[len("postgres://") :])
    else:
        raise RuntimeError("NEON_POSTGRES_STRING must start with postgresql://")

    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    # asyncpg uses `ssl=require` rather than libpq `sslmode=require`
    if "sslmode" in query:
        sslmode = query.pop("sslmode")
        if sslmode and "ssl" not in query:
            query["ssl"] = "require" if sslmode in {"require", "verify-full", "verify-ca"} else sslmode
    if "channel_binding" in query:
        query.pop("channel_binding", None)
    return urlunparse(parsed._replace(query=urlencode(query)))


def to_psycopg_url(dsn: str) -> str:
    """Normalize to postgresql:// for psycopg2 (bulk COPY / CLI)."""
    url = dsn.strip()
    if url.startswith("postgresql+asyncpg://"):
        return "postgresql://" + url[len("postgresql+asyncpg://") :]
    if url.startswith("postgres://"):
        return "postgresql://" + url[len("postgres://") :]
    if url.startswith("postgresql://"):
        return url
    raise RuntimeError("NEON_POSTGRES_STRING must start with postgresql://")


@lru_cache
def get_settings() -> Settings:
    return Settings()
