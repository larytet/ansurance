"""T-M0-2."""

from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

from app.models import Base

BACKEND = Path(__file__).resolve().parent.parent


def _cfg(url: str, monkeypatch) -> Config:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("ANS_DATABASE_URL", url)
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    return cfg


def test_upgrade_from_empty_matches_models_and_downgrades(pg_url: str, monkeypatch) -> None:
    from tests import conftest

    conftest._migrated.discard(pg_url)  # this test leaves the schema re-created  # type: ignore[no-untyped-def]
    eng = create_engine(pg_url)
    with eng.begin() as c:
        c.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    cfg = _cfg(pg_url, monkeypatch)

    command.upgrade(cfg, "head")
    assert "platform_info" in inspect(eng).get_table_names()
    with eng.connect() as c:
        assert (
            c.execute(text("SELECT value FROM platform_info WHERE key='schema_era'")).scalar_one()
            == "m0"
        )
        diff = compare_metadata(MigrationContext.configure(c), Base.metadata)
    assert diff == [], f"model drift: {diff}"

    command.downgrade(cfg, "base")
    assert "platform_info" not in inspect(eng).get_table_names()
    command.upgrade(cfg, "head")  # and up again
    eng.dispose()
