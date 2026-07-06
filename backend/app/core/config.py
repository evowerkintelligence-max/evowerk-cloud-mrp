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

    cors_origins: list[str] = ["http://localhost:5173"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
