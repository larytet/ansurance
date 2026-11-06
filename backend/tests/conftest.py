import os
from collections.abc import Iterator

import pytest
from pydantic import SecretStr
from sqlalchemy import Engine, create_engine

from app.config import Profile, Settings


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
