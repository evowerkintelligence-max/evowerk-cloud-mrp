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
    rate_limit_register_per_hour: int = 20
    rate_limit_login_per_5min: int = 60
    rate_limit_refresh_per_5min: int = 120
    rate_limit_mfa_verify_per_5min: int = 30

    cors_origins: list[str] = ["http://localhost:5173"]

    # Ad-hoc dataset import layer: uploaded files are stored under this root,
    # partitioned per org. Local filesystem for now; an object-storage backend
    # slots in behind app/services/storage.py later.
    data_dir: str = "./data"
    max_upload_mb: int = 100
    allowed_dataset_exts: list[str] = [
        "csv", "tsv", "xlsx", "xls", "json", "jsonl", "ndjson", "parquet",
    ]


@lru_cache
def get_settings() -> Settings:
    return Settings()
