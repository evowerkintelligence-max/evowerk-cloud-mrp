from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    env: str = "dev"

    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/evowerk_cloud_mrp"

    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 30

    totp_issuer: str = "EvoWerk Cloud MRP"
    # Fernet key (Fernet.generate_key()) used to encrypt TOTP secrets at rest.
    mfa_encryption_key: str

    max_failed_login_attempts: int = 5
    account_lockout_minutes: int = 15

    # Per-IP rate limits (on top of the per-account lockout above). These guard
    # against credential stuffing / mass account creation from a single source,
    # not against a single legitimate account being brute-forced (that's the
    # lockout's job). Deliberately generous: many real users can share one public
    # IP (office NAT, VPN, campus network), so limits must comfortably clear at
    # least a few dozen concurrent legitimate users, not just ~10.
    redis_url: str = "redis://localhost:6379/0"
    # docker-compose's redpanda service only publishes its external listener
    # on 19092 to the host (9092 is internal-only, for other containers) -
    # this default matches running the backend on the host against compose.
    redpanda_brokers: str = "localhost:19092"
    rate_limit_register_per_hour: int = 20
    rate_limit_login_per_5min: int = 60
    rate_limit_refresh_per_5min: int = 120
    rate_limit_mfa_verify_per_5min: int = 30
    # Free-text SQL is materially more expensive than the allow-listed query
    # builder (full dataset load + arbitrary joins/aggregates per call), so
    # unlike /datasets/{id}/query it gets its own limit.
    rate_limit_sql_query_per_5min: int = 30
    sql_query_timeout_seconds: float = 10.0
    # Caps per-connection RAM so a runaway query (e.g. a self cross-join) can't
    # balloon past this before the wall-clock timeout above gets a chance to fire.
    duckdb_memory_limit: str = "1GB"

    cors_origins: list[str] = ["http://localhost:5173"]

    # Analytics dataset layer: uploaded production data is stored under this root,
    # partitioned per org. Local filesystem for now; an object-storage backend
    # (S3-compatible) slots in behind app/services/storage.py later.
    data_dir: str = "./data"
    max_upload_mb: int = 100
    allowed_dataset_exts: list[str] = [
        "csv", "tsv", "xlsx", "xls", "json", "jsonl", "ndjson", "parquet",
    ]

    # Gemini client for LLM-backed features (data chat, computer vision, report
    # narration). Optional: unset means every caller uses its deterministic
    # fallback path instead of a live model call - see app/services/llm.py.
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-2.5-flash"


@lru_cache
def get_settings() -> Settings:
    return Settings()
