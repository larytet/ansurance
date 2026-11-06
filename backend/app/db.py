"""Database engine. Sync SQLAlchemy 2 with psycopg 3."""

from sqlalchemy import Engine, create_engine, text

from app.config import Settings


def make_engine(settings: Settings) -> Engine:
    return create_engine(settings.database_url.get_secret_value(), pool_pre_ping=True)


def database_ready(engine: Engine) -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        return False
    return True
