"""T-M1-6: OIDC login against a fake provider. Also P-10 (OIDC abuse)."""

import time
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from authlib.jose import JsonWebKey, jwt
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from app.auth import service as auth
from app.config import Settings, SsoProvider
from app.main import create_app

ISSUER = "https://idp.example"
CLIENT_ID = "ansurance-client"
TOKEN_URL, JWKS_URL = f"{ISSUER}/token", f"{ISSUER}/jwks"


class FakeIdp:
    def __init__(self) -> None:
        self.key = JsonWebKey.generate_key("RSA", 2048, is_private=True, options={"kid": "k1"})
        self.nonce = ""
        self.claims: dict[str, object] = {}
        self.sign_key = self.key

    def id_token(self, **over: object) -> str:
        claims = {
            "iss": ISSUER,
            "aud": CLIENT_ID,
            "sub": "sub-1",
            "email": "sso@agency.il",
            "email_verified": True,
            "nonce": self.nonce,
            "iat": int(time.time()),
            "exp": int(time.time()) + 300,
            **over,
        }
        return jwt.encode({"alg": "RS256", "kid": "k1"}, claims, self.sign_key).decode()

    def handler(self, request: httpx.Request) -> httpx.Response:
        if str(request.url) == JWKS_URL:
            return httpx.Response(200, json={"keys": [self.key.as_dict(is_private=False)]})
        if str(request.url) == TOKEN_URL:
            form = parse_qs(request.content.decode())
            assert form["code_verifier"][0] and form["grant_type"] == ["authorization_code"]
            return httpx.Response(200, json={"id_token": self.pending})
        return httpx.Response(404)

    pending = ""


@pytest.fixture
def idp() -> FakeIdp:
    return FakeIdp()


@pytest.fixture
def sso_api(settings: Settings, fresh_db: Engine, idp: FakeIdp) -> TestClient:
    provider = SsoProvider(
        name="m365",
        client_id=CLIENT_ID,
        client_secret=SecretStr("s3cret"),
        authorize_url=f"{ISSUER}/authorize",
        token_url=TOKEN_URL,
        jwks_url=JWKS_URL,
        issuer=ISSUER,
        redirect_uri="https://app.example/api/auth/sso/m365/callback",
    )
    s = settings.model_copy(update={"sso_providers": [provider]})
    http = httpx.Client(transport=httpx.MockTransport(idp.handler))
    return TestClient(create_app(s, fresh_db, http), base_url="https://testserver")


def start(api: TestClient, idp: FakeIdp) -> str:
    r = api.get("/api/auth/sso/m365/start", follow_redirects=False)
    assert r.status_code == 302
    q = parse_qs(urlparse(r.headers["location"]).query)
    assert q["code_challenge_method"] == ["S256"] and q["code_challenge"][0]
    idp.nonce = q["nonce"][0]
    return q["state"][0]


def sso_user(db: Session, email: str = "sso@agency.il") -> None:
    auth.create_user(db, email, "Sso User", ["agent"], auth_mode="sso")
    db.commit()


def callback(api: TestClient, idp: FakeIdp, state: str, token: str | None = None) -> int:
    idp.pending = token if token is not None else idp.id_token()
    return api.get("/api/auth/sso/m365/callback", params={"code": "c", "state": state}).status_code


def test_sso_login_success_links_identity(sso_api: TestClient, idp: FakeIdp, db: Session) -> None:
    sso_user(db)
    assert callback(sso_api, idp, start(sso_api, idp)) == 200
    assert sso_api.get("/api/auth/me").json()["email"] == "sso@agency.il"
    assert db.execute(text("SELECT count(*) FROM sso_identity")).scalar_one() == 1


def test_unknown_user_rejected_and_not_provisioned(
    sso_api: TestClient, idp: FakeIdp, db: Session
) -> None:
    assert callback(sso_api, idp, start(sso_api, idp)) == 403
    assert db.execute(text("SELECT count(*) FROM user_account")).scalar_one() == 0
    assert sso_api.get("/api/auth/me").status_code == 401
    assert (
        db.execute(
            text("SELECT count(*) FROM audit_entry WHERE action = 'login.fail_sso_unknown'")
        ).scalar_one()
        == 1
    )


def test_local_user_cannot_log_in_via_sso(sso_api: TestClient, idp: FakeIdp, db: Session) -> None:
    auth.create_user(db, "sso@agency.il", "Local", ["agent"], "a-fine-password-1")
    db.commit()
    assert callback(sso_api, idp, start(sso_api, idp)) == 403


def test_state_is_single_use_replay_rejected(
    sso_api: TestClient, idp: FakeIdp, db: Session
) -> None:
    sso_user(db)
    state = start(sso_api, idp)
    assert callback(sso_api, idp, state) == 200
    assert callback(sso_api, idp, state) == 400


def test_unknown_state_rejected(sso_api: TestClient, idp: FakeIdp, db: Session) -> None:
    sso_user(db)
    start(sso_api, idp)
    assert callback(sso_api, idp, "forged-state") == 400


def test_wrong_nonce_rejected(sso_api: TestClient, idp: FakeIdp, db: Session) -> None:
    sso_user(db)
    state = start(sso_api, idp)
    assert callback(sso_api, idp, state, idp.id_token(nonce="other")) == 401


@pytest.mark.parametrize(
    "over",
    [
        {"aud": "someone-else"},
        {"iss": "https://evil.example"},
        {"email_verified": False},
        {"exp": 1},
    ],
)
def test_bad_claims_rejected(
    sso_api: TestClient, idp: FakeIdp, db: Session, over: dict[str, object]
) -> None:
    sso_user(db)
    state = start(sso_api, idp)
    assert callback(sso_api, idp, state, idp.id_token(**over)) == 401
    assert sso_api.get("/api/auth/me").status_code == 401


def test_token_signed_with_other_key_rejected(
    sso_api: TestClient, idp: FakeIdp, db: Session
) -> None:
    sso_user(db)
    state = start(sso_api, idp)
    idp.sign_key = JsonWebKey.generate_key("RSA", 2048, is_private=True, options={"kid": "k1"})
    assert callback(sso_api, idp, state) == 401


def test_unknown_provider_404(sso_api: TestClient) -> None:
    assert sso_api.get("/api/auth/sso/nope/start", follow_redirects=False).status_code == 404
