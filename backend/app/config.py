"""Settings. One image, several profiles (onprem, cloud, demo)."""

from enum import StrEnum
from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Profile(StrEnum):
    ONPREM = "onprem"
    CLOUD = "cloud"
    DEMO = "demo"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ANS_", env_file=None, extra="ignore")

    profile: Profile
    database_url: SecretStr
    # S3 API endpoint. Empty means real AWS S3 (cloud profile).
    s3_endpoint: str = ""
    s3_region: str = "il-central-1"
    s3_bucket_originals: str = "originals"
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # values come from the environment
