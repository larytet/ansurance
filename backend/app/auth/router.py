from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session as Db

from app.audit import chain
from app.auth import service as auth
from app.auth import sso
from app.auth.passwords import check_policy
from app.capabilities import ROLES
from app.config import Settings
from app.deps import (
    SESSION_COOKIE,
    Principal,
    current_principal,
    get_db,
    get_settings_dep,
    require,
)
from app.models import Session

router = APIRouter(prefix="/api")


class LoginIn(BaseModel):
    email: str
    password: str
    code: str


class UserIn(BaseModel):
    email: str
    display_name: str
    roles: list[str]
    auth_mode: str = "local"
    password: str | None = None


def _set_cookie(resp: Response, settings: Settings, token: str) -> None:
    resp.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=settings.session_absolute_hours * 3600,
        path="/",
    )


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _me(p: Principal) -> dict[str, Any]:
    return {
        "id": str(p.user.id),
        "email": p.user.email,
        "name": p.user.display_name,
        "roles": p.roles,
        "capabilities": sorted(p.capabilities),
    }


@router.post("/auth/login")
def login(
    body: LoginIn,
    request: Request,
    response: Response,
    db: Db = Depends(get_db, scope="function"),
    settings: Settings = Depends(get_settings_dep),
) -> dict[str, Any]:
    try:
        new = auth.login_local(
            db,
            settings,
            body.email,
            body.password,
            body.code,
            _client_ip(request),
            request.headers.get("user-agent"),
        )
    except auth.AuthError as e:
        raise HTTPException(e.status, e.code) from e
    _set_cookie(response, settings, new.token)
    return {"csrf": new.csrf, "user_id": str(new.session.user_id)}


@router.post("/auth/logout")
def logout(
    response: Response,
    p: Principal = Depends(current_principal),
    db: Db = Depends(get_db, scope="function"),
) -> dict[str, str]:
    auth.revoke(db, p.session_id, str(p.user.id))
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"status": "logged_out"}


@router.get("/auth/me")
def me(p: Principal = Depends(current_principal)) -> dict[str, Any]:
    return {**_me(p), "csrf": p.csrf}


@router.get("/auth/sessions")
def sessions(
    p: Principal = Depends(current_principal), db: Db = Depends(get_db, scope="function")
) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(Session)
        .where(Session.user_id == p.user.id, Session.revoked_at.is_(None))
        .order_by(Session.created_at)
    )
    return [
        {
            "id": str(s.id),
            "ip": s.ip,
            "user_agent": s.user_agent,
            "created_at": s.created_at.isoformat(),
            "current": s.id == p.session_id,
        }
        for s in rows
    ]


@router.delete("/auth/sessions/{session_id}")
def revoke_session(
    session_id: str,
    p: Principal = Depends(current_principal),
    db: Db = Depends(get_db, scope="function"),
) -> dict[str, str]:
    s = db.get(Session, session_id)
    if s is None or s.user_id != p.user.id:
        raise HTTPException(404, "not_found")
    auth.revoke(db, s.id, str(p.user.id))
    return {"status": "revoked"}


@router.post("/users", status_code=201)
def create_user(
    body: UserIn,
    p: Principal = Depends(require("user.manage")),
    db: Db = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    bad = [r for r in body.roles if r not in ROLES]
    if bad or not body.roles:
        raise HTTPException(422, f"unknown or missing roles: {bad}")
    if body.auth_mode not in ("local", "sso"):
        raise HTTPException(422, "auth_mode")
    if body.auth_mode == "local":
        reason = check_policy(body.password or "", body.email)
        if reason:
            raise HTTPException(422, f"weak_password: {reason}")
    try:
        user, extra = auth.create_user(
            db, body.email, body.display_name, body.roles, body.password, body.auth_mode
        )
    except Exception as e:  # duplicate e-mail
        db.rollback()
        raise HTTPException(409, "user_exists") from e
    chain.append(
        db,
        str(p.user.id),
        "user.create",
        f"user:{user.id}",
        {"roles": body.roles, "auth_mode": body.auth_mode},
    )
    return {"id": str(user.id), **extra}


@router.get("/auth/sso/{provider}/start")
def sso_start(
    provider: str,
    db: Db = Depends(get_db, scope="function"),
    settings: Settings = Depends(get_settings_dep),
) -> Response:
    try:
        url = sso.start(db, settings, provider)
    except auth.AuthError as e:
        raise HTTPException(e.status, e.code) from e
    return Response(status_code=302, headers={"location": url})


@router.get("/auth/sso/{provider}/callback")
def sso_callback(
    provider: str,
    code: str,
    state: str,
    request: Request,
    response: Response,
    db: Db = Depends(get_db, scope="function"),
    settings: Settings = Depends(get_settings_dep),
) -> dict[str, Any]:
    try:
        new = sso.callback(
            db,
            settings,
            provider,
            code,
            state,
            _client_ip(request),
            request.headers.get("user-agent"),
            request.app.state.http,
        )
    except auth.AuthError as e:
        db.commit()  # keep the failure audit entry
        raise HTTPException(e.status, e.code) from e
    _set_cookie(response, settings, new.token)
    return {"csrf": new.csrf, "user_id": str(new.session.user_id)}
