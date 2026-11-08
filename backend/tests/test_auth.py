"""T-M1-1 .. T-M1-5."""

from datetime import UTC, datetime, timedelta

import pyotp
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from app.auth.passwords import check_policy, hash_password, verify_password
from app.config import Settings
from app.main import create_app
from tests.conftest import PASSWORD, Account, login, make_account


def post_login(
    api: TestClient, acct: Account, password: str | None = None, code: str | None = None
) -> int:
    return api.post(
        "/api/auth/login",
        json={
            "email": acct.email,
            "password": password or acct.password,
            "code": code or acct.code(),
        },
    ).status_code


# T-M1-1 ------------------------------------------------------------------------------
def test_argon2id_hash_and_verify() -> None:
    h = hash_password(PASSWORD)
    assert h.startswith("$argon2id$")
    assert verify_password(h, PASSWORD)
    assert not verify_password(h, PASSWORD + "x")
    assert not verify_password(None, PASSWORD)


def test_password_policy() -> None:
    assert check_policy("short") is not None
    assert check_policy("password") is not None
    assert check_policy("aaaaaaaaaaaaaaaa") is not None
    assert check_policy("alice-and-more-1", "alice@x.il") is not None  # based on the e-mail
    assert check_policy("correct-horse-battery", "alice@x.il") is None


# T-M1-2 / T-M1-3 / T-M1-4 --------------------------------------------------------------
def test_login_success_and_me(api: TestClient, db: Session) -> None:
    a = make_account(db, "ann@agency.il", ["agent"])
    login(api, db, a)
    me = api.get("/api/auth/me").json()
    assert me["email"] == "ann@agency.il" and me["roles"] == ["agent"]
    assert "client.write" in me["capabilities"] and "audit.read" not in me["capabilities"]


def test_wrong_password_and_unknown_user_are_indistinguishable(
    api: TestClient, db: Session
) -> None:
    a = make_account(db, "ann@agency.il", ["agent"])
    r1 = api.post(
        "/api/auth/login", json={"email": a.email, "password": "nope-nope-nope-1", "code": a.code()}
    )
    r2 = api.post(
        "/api/auth/login",
        json={"email": "ghost@agency.il", "password": "nope-nope-nope-1", "code": "000000"},
    )
    assert r1.status_code == r2.status_code == 401 and r1.json() == r2.json()


def test_wrong_totp_rejected_and_code_is_single_use(api: TestClient, db: Session) -> None:
    a = make_account(db, "ann@agency.il", ["agent"])
    assert post_login(api, a, code="000000") == 401
    code = a.code()
    db.execute(text("UPDATE user_account SET totp_last_counter = 0"))
    db.commit()
    assert post_login(api, a, code=code) == 200
    assert post_login(api, a, code=code) == 401  # replay of the same code


def test_totp_allows_one_step_of_clock_skew(api: TestClient, db: Session) -> None:
    a = make_account(db, "ann@agency.il", ["agent"])
    import time

    past = pyotp.TOTP(a.secret).at(int(time.time()) - 30)
    assert post_login(api, a, code=past) == 200
    a2 = make_account(db, "bob@agency.il", ["agent"])
    old = pyotp.TOTP(a2.secret).at(int(time.time()) - 120)
    assert post_login(api, a2, code=old) == 401


def test_recovery_code_single_use(api: TestClient, db: Session) -> None:
    a = make_account(db, "ann@agency.il", ["agent"])
    rc = a.recovery_codes[0]  # type: ignore[attr-defined]
    assert post_login(api, a, code=rc) == 200
    assert post_login(api, a, code=rc) == 401
    assert post_login(api, a, code=a.recovery_codes[1]) == 200  # type: ignore[attr-defined]


def test_lockout_after_threshold_then_blocks_even_correct_credentials(
    api: TestClient, db: Session
) -> None:
    a = make_account(db, "ann@agency.il", ["agent"])
    for _ in range(5):
        assert post_login(api, a, password="wrong-wrong-wrong") == 401
    assert post_login(api, a) == 423  # correct credentials, still locked
    db.execute(
        text("UPDATE user_account SET locked_until = :t"),
        {"t": datetime.now(UTC) - timedelta(seconds=1)},
    )
    db.commit()
    assert post_login(api, a) == 200  # lock expired


def test_ip_rate_limit(api: TestClient, db: Session, settings) -> None:  # type: ignore[no-untyped-def]
    for i in range(settings.ip_failure_limit):
        api.post(
            "/api/auth/login",
            json={"email": f"x{i}@agency.il", "password": "w" * 14, "code": "000000"},
        )
    r = api.post(
        "/api/auth/login", json={"email": "y@agency.il", "password": "w" * 14, "code": "000000"}
    )
    assert r.status_code == 429


def test_login_events_are_audited(api: TestClient, db: Session) -> None:
    a = make_account(db, "ann@agency.il", ["agent"])
    post_login(api, a, password="wrong-wrong-wrong")
    login(api, db, a)
    actions = [r[0] for r in db.execute(text("SELECT action FROM audit_entry ORDER BY seq"))]
    assert "login.fail_password" in actions and "login.success" in actions


# T-M1-5 sessions ------------------------------------------------------------------------
def test_cookie_flags(api: TestClient, db: Session) -> None:
    a = make_account(db, "ann@agency.il", ["agent"])
    db.execute(text("UPDATE user_account SET totp_last_counter = 0"))
    db.commit()
    r = api.post(
        "/api/auth/login", json={"email": a.email, "password": a.password, "code": a.code()}
    )
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "secure" in cookie and "samesite=lax" in cookie


def test_requires_login_and_csrf(api: TestClient, db: Session) -> None:
    assert api.get("/api/auth/me").status_code == 401
    a = make_account(db, "ann@agency.il", ["agent"])
    hdr = login(api, db, a)
    assert api.post("/api/auth/logout").status_code == 403  # no CSRF header
    assert api.post("/api/auth/logout", headers={"x-csrf-token": "wrong"}).status_code == 403
    assert api.post("/api/auth/logout", headers=hdr).status_code == 200
    assert api.get("/api/auth/me").status_code == 401  # session is gone


def test_idle_and_absolute_timeouts(api: TestClient, db: Session, settings) -> None:  # type: ignore[no-untyped-def]
    a = make_account(db, "ann@agency.il", ["agent"])
    login(api, db, a)
    assert api.get("/api/auth/me").status_code == 200
    old = datetime.now(UTC) - timedelta(minutes=settings.session_idle_minutes + 1)
    db.execute(text("UPDATE session SET last_seen = :t"), {"t": old})
    db.commit()
    assert api.get("/api/auth/me").status_code == 401  # idle timeout

    login(api, db, a)
    db.execute(
        text("UPDATE session SET expires_at = :t WHERE revoked_at IS NULL"),
        {"t": datetime.now(UTC) - timedelta(seconds=1)},
    )
    db.commit()
    assert api.get("/api/auth/me").status_code == 401  # absolute timeout


def test_device_list_and_revoke(api: TestClient, db: Session) -> None:
    a = make_account(db, "ann@agency.il", ["agent"])
    hdr = login(api, db, a)
    other = TestClient(api.app, base_url="https://testserver")  # type: ignore[arg-type]
    login(other, db, a)
    sessions = api.get("/api/auth/sessions").json()
    assert len(sessions) == 2 and sum(s["current"] for s in sessions) == 1
    victim = next(s for s in sessions if not s["current"])
    assert api.delete(f"/api/auth/sessions/{victim['id']}", headers=hdr).status_code == 200
    assert other.get("/api/auth/me").status_code == 401  # revoked device is out
    assert api.get("/api/auth/me").status_code == 200


def test_cannot_revoke_someone_elses_session(api: TestClient, db: Session) -> None:
    a = make_account(db, "ann@agency.il", ["agent"])
    b = make_account(db, "bob@agency.il", ["agent"])
    other = TestClient(api.app, base_url="https://testserver")  # type: ignore[arg-type]
    login(other, db, b)
    sid = other.get("/api/auth/sessions").json()[0]["id"]
    hdr = login(api, db, a)
    assert api.delete(f"/api/auth/sessions/{sid}", headers=hdr).status_code == 404


def test_manager_creates_users_with_policy(api: TestClient, db: Session) -> None:
    mgr = make_account(db, "mgr@agency.il", ["manager"])
    hdr = login(api, db, mgr)
    body = {
        "email": "new@agency.il",
        "display_name": "New",
        "roles": ["agent"],
        "password": "a-fine-password-1",
    }
    r = api.post("/api/users", json=body, headers=hdr)
    assert r.status_code == 201 and r.json()["totp_uri"].startswith("otpauth://")
    assert len(r.json()["recovery_codes"]) == 10
    assert api.post("/api/users", json=body, headers=hdr).status_code == 409
    weak = {**body, "email": "w@agency.il", "password": "short"}
    assert api.post("/api/users", json=weak, headers=hdr).status_code == 422
    bad_role = {**body, "email": "r@agency.il", "roles": ["god"]}
    assert api.post("/api/users", json=bad_role, headers=hdr).status_code == 422


# ---- the real client address behind a proxy ----
def _fail(c: TestClient, who: str, xff: str | None) -> int:
    headers = {"x-forwarded-for": xff} if xff else {}
    return c.post(
        "/api/auth/login",
        headers=headers,
        json={"email": who, "password": "w" * 14, "code": "000000"},
    ).status_code


def test_behind_a_trusted_proxy_the_limit_counts_each_client_separately(
    settings: Settings, fresh_db: Engine, db: Session
) -> None:
    """A single attacker must not be able to lock the whole office out by exhausting the
    per-IP limit while every request appears to come from the proxy."""
    s = settings.model_copy(update={"trusted_proxies": "*", "ip_failure_limit": 3})
    c = TestClient(create_app(s, fresh_db), base_url="https://testserver")
    for _ in range(3):
        assert _fail(c, "x@agency.il", "198.51.100.9") == 401
    assert _fail(c, "x@agency.il", "198.51.100.9") == 429  # this client is throttled
    assert _fail(c, "y@agency.il", "203.0.113.5") == 401  # everybody else is not
    ips = {r[0] for r in db.execute(text("SELECT DISTINCT ip FROM login_event"))}
    assert {"198.51.100.9", "203.0.113.5"} <= ips and "testclient" not in ips


def test_forwarded_header_is_ignored_when_the_sender_is_not_a_trusted_proxy(
    settings: Settings, fresh_db: Engine, db: Session
) -> None:
    """Someone reaching the API directly cannot choose their own address."""
    s = settings.model_copy(update={"ip_failure_limit": 3})  # default: private ranges only
    c = TestClient(create_app(s, fresh_db), base_url="https://testserver")
    for i in range(3):
        _fail(c, f"u{i}@agency.il", f"198.51.100.{i}")  # a different forged address each time
    assert _fail(c, "u9@agency.il", "198.51.100.77") == 429  # still one client: no evasion
    ips = {r[0] for r in db.execute(text("SELECT DISTINCT ip FROM login_event"))}
    assert not any(ip.startswith("198.51.100.") for ip in ips)


def test_password_min_length_is_configurable() -> None:
    assert check_policy("abcd-efg9") is None  # default minimum is 8
    assert check_policy("abc-ef9") is not None  # 7 characters
    assert check_policy("abcd-efg9", min_length=12) is not None


def test_short_email_names_do_not_block_ordinary_passwords() -> None:
    assert check_policy("another-pass-9", "a@a.com") is None  # starts with "a", name is 1 letter
    assert check_policy("alice-and-more-1", "alice@x.il") == "based on the e-mail"
    assert check_policy("password") == "too common"
