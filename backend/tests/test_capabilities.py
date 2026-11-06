"""The Python constants and the seeded database must agree (a change needs a migration)."""

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.capabilities import CAPABILITIES, ROLE_CAPABILITIES


def test_database_seed_matches_constants(db: Session) -> None:
    assert {r[0] for r in db.execute(text("SELECT name FROM capability"))} == set(CAPABILITIES)
    assert {r[0] for r in db.execute(text("SELECT name FROM role"))} == set(ROLE_CAPABILITIES)
    rows = db.execute(text("SELECT role, capability FROM role_capability")).all()
    assert {(r, c) for r, c in rows} == {
        (role, cap) for role, caps in ROLE_CAPABILITIES.items() for cap in caps
    }


def test_marketing_has_no_client_or_document_access() -> None:
    caps = set(ROLE_CAPABILITIES["marketing"])
    assert not {c for c in caps if c.startswith(("client.read", "document", "policy"))} - {
        "client.read_min"
    }
