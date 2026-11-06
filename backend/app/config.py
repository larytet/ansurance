"""Settings. One image, several profiles (onprem, cloud, demo)."""

from enum import StrEnum
from functools import lru_cache

from pydantic import BaseModel, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Profile(StrEnum):
    ONPREM = "onprem"
    CLOUD = "cloud"
    DEMO = "demo"


class SsoProvider(BaseModel):
    """An OIDC provider (Microsoft 365, Google Workspace). Authorization code + PKCE."""

    name: str
    client_id: str
    client_secret: SecretStr
    authorize_url: str
    token_url: str
    jwks_url: str
    issuer: str
    redirect_uri: str
    scope: str = "openid email profile"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ANS_", env_file=None, extra="ignore")

    profile: Profile
    database_url: SecretStr
    # S3 API endpoint. Empty means real AWS S3 (cloud profile).
    s3_endpoint: str = ""
    s3_region: str = "il-central-1"
    s3_bucket_originals: str = "originals"
    log_level: str = "INFO"
    # sessions
    session_idle_minutes: int = 30
    session_absolute_hours: int = 12
    cookie_secure: bool = True
    # lockout: after N failures lock for 2^(n-N) minutes (max 60); per-IP failure ceiling
    lockout_threshold: int = 5
    ip_failure_limit: int = 20
    ip_failure_window_minutes: int = 15
    sso_providers: list[SsoProvider] = []


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # values come from the environment
