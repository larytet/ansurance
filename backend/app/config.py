"""Settings. One image, several profiles (onprem, cloud, demo)."""

from enum import StrEnum
from functools import lru_cache
from urllib.parse import quote

from pydantic import BaseModel, SecretStr, model_validator
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
    # Either a full URL, or its parts (AWS: host and name from the task definition, the
    # password injected from the RDS-managed secret).
    database_url: SecretStr = SecretStr("")
    db_host: str = ""
    db_port: int = 5432
    db_name: str = "ansurance"
    db_user: str = "ansurance"
    db_password: SecretStr = SecretStr("")
    db_sslmode: str = "require"
    # S3 API endpoint. Empty means real AWS S3 (cloud profile).
    s3_endpoint: str = ""
    s3_access_key: str = ""
    s3_secret_key: SecretStr | None = None
    s3_region: str = "il-central-1"
    s3_bucket_originals: str = "originals"
    s3_bucket_audit_archive: str = "audit-archive"
    log_level: str = "INFO"
    # audit of reads is asynchronous and batched (see app/audit/readbuffer.py)
    read_audit_flush_seconds: float = 5.0
    read_audit_batch: int = 200
    # Proxies whose X-Forwarded-For is believed (Caddy on-prem, the ALB on AWS). Only these
    # addresses may reach the API, so private ranges are the safe default; set "*" only if the
    # network guarantees that nothing else can connect.
    trusted_proxies: str = "127.0.0.1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"
    # sessions
    session_idle_minutes: int = 30
    session_absolute_hours: int = 12
    cookie_secure: bool = True
    # lockout: after N failures lock for 2^(n-N) minutes (max 60); per-IP failure ceiling
    lockout_threshold: int = 5
    ip_failure_limit: int = 20
    ip_failure_window_minutes: int = 15
    sso_providers: list[SsoProvider] = []
    # documents
    max_upload_bytes: int = 100 * 1024 * 1024
    retention_years_default: int = 7
    retention_years_by_type: dict[str, int] = {}
    clamd_host: str = ""  # empty: fake scanner in the demo profile
    clamd_port: int = 3310
    # intake
    ocr_langs: str = "heb+ara+rus+fra+eng"
    ocr_engine: str = "tesseract"  # "tesseract" or "fake" (tests, demo)
    intake_auto_threshold: float = 0.95
    fax_dir: str = ""  # watched drop folder; empty disables the fax channel
    fax_poll_seconds: int = 30
    render_dpi: int = 200
    # integrations: an adapter runs only if it is both configured and on the approved list
    approved_adapters: list[str] = ["whatsapp", "imap", "agency", "esign", "translate"]
    whatsapp_phone_id: str = ""
    whatsapp_token: SecretStr | None = None
    whatsapp_app_secret: SecretStr | None = None  # verifies webhook signatures
    whatsapp_verify_token: str = ""
    whatsapp_api_base: str = "https://graph.facebook.com/v20.0"
    imap_host: str = ""
    imap_port: int = 993
    imap_user: str = ""
    imap_password: SecretStr | None = None
    imap_folder: str = "INBOX"
    mailbox_poll_seconds: int = 60
    tax_cert_wait_days: int = 30  # a requested certificate not received by then raises an item
    tax_deadline_warn_days: int = 14
    portal_link_days: int = 7
    complaint_sla_days: int = 14
    kyc_valid_days: int = 365
    licence_warn_days: int = 60
    commission_tolerance_ils: float = 0.05
    campaign_batch_size: int = 50  # recipients queued per worker run (throttling)
    campaign_batch_seconds: int = 60  # pause between batches
    campaign_frequency_days: int = 30  # one marketing contact per client in this period
    esign_webhook_secret: SecretStr | None = None
    esign_validity_months: int = 12  # signed data-lookup authorizations (verify per form)
    esign_warn_days: int = 183  # flag authorizations that expire within about six months
    max_delivery_attempts: int = 5
    default_country_code: str = "972"
    daily_seconds: int = 86_400  # sealing, nightly audit verification, backup-age check
    backup_max_age_hours: int = 26
    second_pass_below: float = 0.80  # re-read with the Latin model when IDs read below this

    @model_validator(mode="after")
    def _database_url_from_parts(self) -> "Settings":
        if not self.database_url.get_secret_value():
            if not (self.db_host and self.db_password.get_secret_value()):
                raise ValueError("set ANS_DATABASE_URL, or ANS_DB_HOST and ANS_DB_PASSWORD")
            pw = quote(self.db_password.get_secret_value(), safe="")
            self.database_url = SecretStr(
                f"postgresql+psycopg://{quote(self.db_user, safe='')}:{pw}@{self.db_host}:"
                f"{self.db_port}/{self.db_name}?sslmode={self.db_sslmode}"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # values come from the environment
