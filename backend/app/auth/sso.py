"""OIDC authorization code flow with PKCE (Microsoft 365, Google Workspace).

A login succeeds only for a user the manager created beforehand: no auto-provisioning.
"""

import base64
import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import httpx
from authlib.jose import JsonWebKey, jwt
from authlib.jose.errors import JoseError
from sqlalchemy import select
from sqlalchemy.orm import Session as Db

from app.audit import chain
from app.auth.service import AuthError, NewSession, start_session
from app.config import Settings, SsoProvider
from app.models import SsoIdentity, SsoState, User

STATE_TTL = timedelta(minutes=10)


def _provider(settings: Settings, name: str) -> SsoProvider:
    for p in settings.sso_providers:
        if p.name == name:
            return p
    raise AuthError("unknown_provider", 404)


def start(db: Db, settings: Settings, provider_name: str) -> str:
    p = _provider(settings, provider_name)
    state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=")
    db.add(SsoState(state=state, provider=p.name, nonce=nonce, code_verifier=verifier))
    db.flush()
    q = urlencode(
        {
            "client_id": p.client_id,
            "response_type": "code",
            "redirect_uri": p.redirect_uri,
            "scope": p.scope,
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge.decode(),
            "code_challenge_method": "S256",
        }
    )
    return f"{p.authorize_url}?{q}"


def callback(
    db: Db,
    settings: Settings,
    provider_name: str,
    code: str,
    state: str,
    ip: str | None,
    agent: str | None,
    http: httpx.Client,
) -> NewSession:
    p = _provider(settings, provider_name)
    st = db.get(SsoState, state)
    if st is None or st.provider != p.name:
        raise AuthError("bad_state", 400)
    db.delete(st)  # single use
    db.flush()
    if datetime.now(UTC) - st.created_at > STATE_TTL:
        raise AuthError("state_expired", 400)

    tok = http.post(
        p.token_url,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": p.redirect_uri,
            "client_id": p.client_id,
            "client_secret": p.client_secret.get_secret_value(),
            "code_verifier": st.code_verifier,
        },
    )
    if tok.status_code != 200 or "id_token" not in tok.json():
        raise AuthError("token_exchange_failed", 401)
    try:
        keys = JsonWebKey.import_key_set(http.get(p.jwks_url).json())
        claims = jwt.decode(tok.json()["id_token"], keys)
        claims.validate()
    except (JoseError, ValueError) as e:
        raise AuthError("bad_id_token", 401) from e
    if claims.get("iss") != p.issuer or claims.get("aud") not in (p.client_id, [p.client_id]):
        raise AuthError("bad_id_token", 401)
    if claims.get("nonce") != st.nonce:
        raise AuthError("bad_nonce", 401)
    sub, email = str(claims.get("sub", "")), str(claims.get("email", "")).lower()
    if not sub or not email or claims.get("email_verified") is not True:
        raise AuthError("unverified_email", 401)

    ident = db.scalar(
        select(SsoIdentity).where(SsoIdentity.provider == p.name, SsoIdentity.subject == sub)
    )
    user = (
        db.get(User, ident.user_id)
        if ident
        else db.scalar(select(User).where(User.email == email, User.auth_mode == "sso"))
    )
    if user is None or not user.active:
        chain.append(db, "anonymous", "login.fail_sso_unknown", f"user:{email}", {"ip": ip or "-"})
        raise AuthError("unknown_user", 403)
    if ident is None:
        db.add(SsoIdentity(user_id=user.id, provider=p.name, subject=sub))
    new = start_session(db, settings, user, ip, agent)
    chain.append(
        db,
        str(user.id),
        "login.success",
        f"user:{email}",
        {"method": f"sso:{p.name}", "ip": ip or "-"},
    )
    return new
