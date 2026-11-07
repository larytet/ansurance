"""Request dependencies: database session, current user, capability checks, CSRF."""

from collections.abc import Callable, Iterator
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session as Db
from sqlalchemy.orm import sessionmaker

from app.audit import chain
from app.auth import service as auth
from app.config import Settings
from app.models import RoleCapability, User, UserRole

SESSION_COOKIE = "ans_session"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def get_settings_dep(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_db(request: Request) -> Iterator[Db]:
    maker: sessionmaker[Db] = request.app.state.sessionmaker
    db = maker()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@dataclass
class Principal:
    user: User
    roles: list[str]
    capabilities: set[str]
    session_id: object
    csrf: str


def current_principal(
    request: Request,
    db: Db = Depends(get_db, scope="function"),
    settings: Settings = Depends(get_settings_dep),
) -> Principal:
    token = request.cookies.get(SESSION_COOKIE)
    loaded = auth.load_session(db, settings, token) if token else None
    if loaded is None:
        raise HTTPException(401, "not_authenticated")
    session, user = loaded
    if request.method not in SAFE_METHODS and request.headers.get("x-csrf-token") != (
        session.csrf_token
    ):
        raise HTTPException(403, "csrf")
    roles = auth.roles_of(db, user.id)
    caps = set(
        db.scalars(
            select(RoleCapability.capability)
            .join(UserRole, UserRole.role == RoleCapability.role)
            .where(UserRole.user_id == user.id)
        )
    )
    return Principal(user, roles, caps, session.id, session.csrf_token)


def require(capability: str) -> Callable[..., Principal]:
    """Single place where capabilities are enforced. Denials are audited."""

    def dep(
        p: Principal = Depends(current_principal), db: Db = Depends(get_db, scope="function")
    ) -> Principal:
        if capability not in p.capabilities:
            chain.append(db, str(p.user.id), "authz.deny", f"cap:{capability}", {})
            db.commit()
            raise HTTPException(403, "forbidden")
        return p

    return dep
