import os
from collections.abc import Iterator
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

    with PostgresContainer("postgres:16-alpine", driver="psycopg") as pg:
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


_migrated: set[str] = set()
_DATA_TABLES = (
    "audit_entry, login_event, sso_state, sso_identity, session, recovery_code, "
    "family_link, external_id, policy, insurer, client, user_role, user_account"
)


@pytest.fixture
def fresh_db(engine: Engine, pg_url: str) -> Engine:
    """Full schema at head with empty data tables (roles and capabilities stay seeded).

    Migrates once per session, then truncates between tests. The audit_entry append-only
    trigger is bypassed for the reset only, with session_replication_role.
    """
    if pg_url not in _migrated:
        with engine.begin() as c:
            c.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
        migrate(pg_url)
        _migrated.add(pg_url)
    with engine.begin() as c:
        c.execute(text("SET session_replication_role = replica"))
        c.execute(text(f"TRUNCATE {_DATA_TABLES} RESTART IDENTITY CASCADE"))
        c.execute(text("SET session_replication_role = DEFAULT"))
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
