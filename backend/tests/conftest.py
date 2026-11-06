import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pyotp
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.auth import service as auth
from app.config import Profile, Settings
from app.main import create_app

BACKEND = Path(__file__).resolve().parent.parent
PASSWORD = "correct-horse-battery"


@pytest.fixture(scope="session")
def pg_url() -> Iterator[str]:
    """A real Postgres. Uses TEST_DATABASE_URL if set, else starts a container."""
    url = os.environ.get("TEST_DATABASE_URL")
    if url:
        yield url
        return
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer(
        "postgres:16-alpine@sha256:0cefaabd882e42bcb2a2f3dc56d3ce4770560ba62fd7f197f1a2e008fd5731ab",
        driver="psycopg",
    ) as pg:
        yield pg.get_connection_url()


@pytest.fixture
def settings(pg_url: str) -> Settings:
    return Settings(profile=Profile.DEMO, database_url=SecretStr(pg_url))


@pytest.fixture
def engine(pg_url: str) -> Iterator[Engine]:
    eng = create_engine(pg_url)
    yield eng
    eng.dispose()


def migrate(url: str) -> None:
    os.environ["ANS_DATABASE_URL"] = url
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    command.upgrade(cfg, "head")


@dataclass(frozen=True)
class MinioInfo:
    endpoint: str
    root_user: str
    root_password: str
    app_user: str
    app_password: str


@pytest.fixture(scope="session")
def minio() -> Iterator[MinioInfo]:
    """The real object store (MinIO built from source), initialised like production."""
    import time
    import urllib.request

    import docker
    from docker.errors import ImageNotFound
    from testcontainers.core.container import DockerContainer

    image = "ansurance-objectstore"
    dc = docker.from_env()
    try:
        dc.images.get(image)
    except ImageNotFound:
        dc.images.build(path=str(BACKEND.parent / "deploy" / "objectstore"), tag=image)
    info = MinioInfo("", "root-user", "root-secret-123", "app-user", "app-secret-1234")
    c = (
        DockerContainer(image)
        .with_command("server /data")
        .with_env("MINIO_ROOT_USER", info.root_user)
        .with_env("MINIO_ROOT_PASSWORD", info.root_password)
        .with_exposed_ports(9000)
    )
    with c:
        endpoint = f"http://{c.get_container_host_ip()}:{c.get_exposed_port(9000)}"
        for _ in range(60):
            try:
                urllib.request.urlopen(endpoint + "/minio/health/live", timeout=2)  # noqa: S310
                break
            except OSError:
                time.sleep(1)
        code, out = c.exec(
            [
                "env",
                "ENDPOINT=http://127.0.0.1:9000",
                f"MINIO_ROOT_USER={info.root_user}",
                f"MINIO_ROOT_PASSWORD={info.root_password}",
                f"APP_USER={info.app_user}",
                f"APP_PASSWORD={info.app_password}",
                "/opt/objectstore/init.sh",
            ]
        )
        assert code == 0, out.decode()
        yield MinioInfo(
            endpoint, info.root_user, info.root_password, info.app_user, info.app_password
        )


@pytest.fixture
def s3_settings(settings: Settings, minio: MinioInfo) -> Settings:
    return settings.model_copy(
        update={
            "s3_endpoint": minio.endpoint,
            "s3_access_key": minio.app_user,
            "s3_secret_key": SecretStr(minio.app_password),
            "s3_region": "us-east-1",
        }
    )


_migrated: set[str] = set()
_DATA_TABLES = (
    "audit_entry, login_event, sso_state, sso_identity, session, recovery_code, "
    "portal_link, translation_record, tax_deadline, tax_residency, tax_certificate, commission_line, commission_statement, commission_term, complaint, agent_licence, campaign_recipient, campaign_translation, outbox_message, campaign, claim_item, claim_event, claim, renewal_outcome, application, advice_record, kyc_form, esign_request, "
    "mail_seen, adapter_call, inbound_message, sync_state, audit_seal, intake_feedback, intake_item, job, "
    "check_instance, stage_instance, process_instance, process_def, "
    "outbox_message, consent_event, inbox_event, inbox_item, derived_artifact, document, "
    "blob, family_link, external_id, policy, insurer, client, user_role, user_account"
)


@pytest.fixture
def fresh_db(engine: Engine, pg_url: str) -> Engine:
    """Full schema at head with empty data tables (roles and capabilities stay seeded).

    Migrates once per session, then truncates between tests. The audit_entry append-only
    trigger is bypassed for the reset only, with session_replication_role.
    """
    from app.audit.readbuffer import discard_all

    discard_all()  # buffers of earlier tests must not write into this one
    if pg_url not in _migrated:
        with engine.begin() as c:
            c.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
        migrate(pg_url)
        _migrated.add(pg_url)
    with engine.begin() as c:
        c.execute(text("SET session_replication_role = replica"))
        c.execute(text(f"TRUNCATE {_DATA_TABLES} RESTART IDENTITY CASCADE"))
        c.execute(text("SET session_replication_role = DEFAULT"))
        c.execute(text("DELETE FROM platform_info WHERE key <> 'schema_era'"))
    return engine


@pytest.fixture
def db(fresh_db: Engine) -> Iterator[Session]:
    s = sessionmaker(fresh_db, expire_on_commit=False)()
    yield s
    s.close()


@pytest.fixture
def api(settings: Settings, fresh_db: Engine) -> TestClient:
    # https so Secure cookies are sent back
    return TestClient(create_app(settings, fresh_db), base_url="https://testserver")


class Account:
    def __init__(self, email: str, secret: str, roles: list[str], user_id: str) -> None:
        self.email, self.secret, self.roles, self.id = email, secret, roles, user_id
        self.password = PASSWORD

    def code(self) -> str:
        return pyotp.TOTP(self.secret).now()


def make_account(db: Session, email: str, roles: list[str]) -> Account:
    user, extra = auth.create_user(db, email, email.split("@")[0], roles, PASSWORD)
    db.commit()
    assert user.totp_secret
    acct = Account(email, user.totp_secret, roles, str(user.id))
    acct.recovery_codes = extra["recovery_codes"]  # type: ignore[attr-defined]
    return acct


def login(api: TestClient, db: Session, acct: Account) -> dict[str, str]:
    """Log in and return the CSRF header for state-changing calls."""
    db.execute(
        text("UPDATE user_account SET totp_last_counter = 0 WHERE email = :e"), {"e": acct.email}
    )
    db.commit()
    r = api.post(
        "/api/auth/login",
        json={"email": acct.email, "password": acct.password, "code": acct.code()},
    )
    assert r.status_code == 200, r.text
    return {"x-csrf-token": r.json()["csrf"]}
