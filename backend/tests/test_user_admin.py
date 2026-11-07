"""T-M11-1 .. T-M11-9: user administration (REQUIREMENTS F16)."""

import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.auth import service as auth
from app.models import User
from tests.conftest import PASSWORD, Account, login, make_account

NEW_PASSWORD = "another-fine-password-2"


def _manager_and_user(api: TestClient, db: Session, roles: list[str] | None = None):  # type: ignore[no-untyped-def]
    mgr = make_account(db, "mgr@agency.il", ["manager"])
    target = make_account(db, "ann@agency.il", roles or ["agent"])
    return mgr, target, login(api, db, mgr)


def _sign_in_status(
    api: TestClient, db: Session, acct: Account, password: str | None = None
) -> int:
    db.execute(text("UPDATE user_account SET totp_last_counter = 0"))  # a code is single use
    db.commit()
    r = api.post(
        "/api/auth/login",
        json={"email": acct.email, "password": password or acct.password, "code": acct.code()},
    )
    return int(r.status_code)


def _fresh_client(api: TestClient) -> TestClient:
    """A second browser: same app, its own cookie jar."""
    return TestClient(api.app, base_url="https://testserver")


def _audit_text(db: Session) -> str:
    rows = db.execute(text("SELECT action, object, detail::text FROM audit_entry")).all()
    return "\n".join(" ".join(r) for r in rows)


# T-M11-1 ------------------------------------------------------------------------------
def test_manager_changes_roles_and_name_audited(api: TestClient, db: Session) -> None:
    mgr, target, hdr = _manager_and_user(api, db)
    r = api.patch(
        f"/api/users/{target.id}",
        json={"roles": ["backoffice"], "display_name": "Ann B"},
        headers=hdr,
    )
    assert r.status_code == 200, r.text
    row = next(u for u in api.get("/api/users").json() if u["id"] == target.id)
    assert row["roles"] == ["backoffice"] and row["name"] == "Ann B"
    assert "user.update" in _audit_text(db)
    unknown = api.patch(f"/api/users/{target.id}", json={"roles": ["god"]}, headers=hdr)
    assert unknown.status_code == 422
    empty = api.patch(f"/api/users/{target.id}", json={"roles": []}, headers=hdr)
    assert empty.status_code == 422
    assert api.patch("/api/users/not-a-uuid", json={}, headers=hdr).status_code == 404


def test_role_change_takes_effect_on_the_next_request(api: TestClient, db: Session) -> None:
    mgr, target, hdr = _manager_and_user(api, db, ["manager"])  # a second manager
    other = _fresh_client(api)
    other_hdr = login(other, db, target)
    assert other.get("/api/users", headers=other_hdr).status_code == 200
    api.patch(f"/api/users/{target.id}", json={"roles": ["agent"]}, headers=hdr)
    assert other.get("/api/users", headers=other_hdr).status_code == 403


# T-M11-2 / T-M11-3 --------------------------------------------------------------------
def test_deactivate_stops_sign_in_and_open_sessions_keeps_the_name(
    api: TestClient, db: Session
) -> None:
    mgr, target, hdr = _manager_and_user(api, db)
    other = _fresh_client(api)
    login(other, db, target)
    assert other.get("/api/auth/me").status_code == 200
    r = api.patch(f"/api/users/{target.id}", json={"active": False}, headers=hdr)
    assert r.status_code == 200
    assert other.get("/api/auth/me").status_code == 401  # next request
    assert _sign_in_status(api, db, target) == 401
    row = next(u for u in api.get("/api/users").json() if u["id"] == target.id)
    assert row["active"] is False and row["name"] == "ann"


def test_reactivate_issues_a_new_authenticator(api: TestClient, db: Session) -> None:
    mgr, target, hdr = _manager_and_user(api, db)
    api.patch(f"/api/users/{target.id}", json={"active": False}, headers=hdr)
    r = api.patch(f"/api/users/{target.id}", json={"active": True}, headers=hdr)
    assert r.status_code == 200
    assert r.json()["totp_uri"].startswith("otpauth://") and len(r.json()["recovery_codes"]) == 10
    assert _sign_in_status(api, db, target) == 401  # the old authenticator is not trusted again
    fresh = Account(
        target.email, pyotp.parse_uri(r.json()["totp_uri"]).secret, ["agent"], target.id
    )
    assert _sign_in_status(api, db, fresh) == 200


# T-M11-4 ------------------------------------------------------------------------------
def test_reset_authenticator_replaces_secret_and_codes_and_revokes_sessions(
    api: TestClient, db: Session
) -> None:
    mgr, target, hdr = _manager_and_user(api, db)
    other = _fresh_client(api)
    login(other, db, target)
    old_code = target.recovery_codes[0]
    r = api.post(f"/api/users/{target.id}/reset-authenticator", headers=hdr)
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["recovery_codes"]) == 10 and not set(body["recovery_codes"]) & set(
        target.recovery_codes
    )
    assert other.get("/api/auth/me").status_code == 401
    old = api.post(
        "/api/auth/login",
        json={"email": target.email, "password": PASSWORD, "code": old_code},
    )
    assert old.status_code == 401  # an old recovery code no longer works
    secret = pyotp.parse_uri(body["totp_uri"]).secret
    assert _sign_in_status(api, db, Account(target.email, secret, ["agent"], target.id)) == 200
    assert "user.reset_authenticator" in _audit_text(db)


# T-M11-5 ------------------------------------------------------------------------------
def test_manager_sets_a_password_policy_enforced_sessions_revoked_lockout_cleared(
    api: TestClient, db: Session
) -> None:
    mgr, target, hdr = _manager_and_user(api, db)
    other = _fresh_client(api)
    login(other, db, target)
    db.execute(
        text("UPDATE user_account SET failed_logins = 9, locked_until = now() + interval '1 hour'")
    )
    db.commit()
    weak = api.post(f"/api/users/{target.id}/password", json={"password": "short"}, headers=hdr)
    assert weak.status_code == 422
    ok = api.post(f"/api/users/{target.id}/password", json={"password": NEW_PASSWORD}, headers=hdr)
    assert ok.status_code == 200
    assert other.get("/api/auth/me").status_code == 401
    assert _sign_in_status(api, db, target, NEW_PASSWORD) == 200
    assert _sign_in_status(api, db, target) == 401  # the old password is gone


# T-M11-6 ------------------------------------------------------------------------------
def test_user_changes_own_password_and_other_devices_are_signed_out(
    api: TestClient, db: Session
) -> None:
    acct = make_account(db, "ann@agency.il", ["agent"])
    other = _fresh_client(api)
    login(other, db, acct)
    hdr = login(api, db, acct)
    db.execute(text("UPDATE user_account SET totp_last_counter = 0"))
    db.commit()
    r = api.post(
        "/api/auth/password",
        json={"current_password": PASSWORD, "code": acct.code(), "new_password": NEW_PASSWORD},
        headers=hdr,
    )
    assert r.status_code == 200, r.text
    assert api.get("/api/auth/me").status_code == 200  # this device stays
    assert other.get("/api/auth/me").status_code == 401
    assert "user.password_change" in _audit_text(db)


def test_wrong_current_password_or_code_is_refused_and_counted(
    api: TestClient, db: Session
) -> None:
    acct = make_account(db, "ann@agency.il", ["agent"])
    hdr = login(api, db, acct)
    db.execute(text("UPDATE user_account SET totp_last_counter = 0"))
    db.commit()
    wrong_pw = api.post(
        "/api/auth/password",
        json={"current_password": "x" * 14, "code": acct.code(), "new_password": NEW_PASSWORD},
        headers=hdr,
    )
    wrong_code = api.post(
        "/api/auth/password",
        json={"current_password": PASSWORD, "code": "000000", "new_password": NEW_PASSWORD},
        headers=hdr,
    )
    assert wrong_pw.status_code == 401 and wrong_code.status_code == 401
    failures = db.scalar(
        text("SELECT failed_logins FROM user_account WHERE email = :e"), {"e": acct.email}
    )
    assert failures == 2
    no_csrf = api.post(
        "/api/auth/password",
        json={"current_password": PASSWORD, "code": acct.code(), "new_password": NEW_PASSWORD},
    )
    assert no_csrf.status_code == 403
    weak = api.post(
        "/api/auth/password",
        json={"current_password": PASSWORD, "code": acct.code(), "new_password": "short"},
        headers=hdr,
    )
    assert weak.status_code == 422


# T-M11-7 ------------------------------------------------------------------------------
def test_the_last_active_manager_is_protected(api: TestClient, db: Session) -> None:
    mgr = make_account(db, "mgr@agency.il", ["manager"])
    hdr = login(api, db, mgr)
    lock_out = api.patch(f"/api/users/{mgr.id}", json={"active": False}, headers=hdr)
    assert lock_out.status_code == 409 and lock_out.json()["detail"] == "last_manager"
    demote = api.patch(f"/api/users/{mgr.id}", json={"roles": ["agent"]}, headers=hdr)
    assert demote.status_code == 409
    assert api.get("/api/users").status_code == 200  # still a manager, still signed in
    second = make_account(db, "mgr2@agency.il", ["manager"])
    ok = api.patch(f"/api/users/{second.id}", json={"active": False}, headers=hdr)
    assert ok.status_code == 200
    # an inactive manager does not count as a second one
    assert api.patch(f"/api/users/{mgr.id}", json={"active": False}, headers=hdr).status_code == 409


# T-M11-8 ------------------------------------------------------------------------------
@pytest.mark.parametrize("role", ["agent", "backoffice", "marketing"])
def test_only_user_manage_may_administer(api: TestClient, db: Session, role: str) -> None:
    victim = make_account(db, "victim@agency.il", ["agent"])
    actor = make_account(db, "actor@agency.il", [role])
    hdr = login(api, db, actor)
    calls = [
        api.patch(f"/api/users/{victim.id}", json={"active": False}, headers=hdr),
        api.patch(f"/api/users/{actor.id}", json={"roles": ["manager"]}, headers=hdr),
        api.post(f"/api/users/{victim.id}/reset-authenticator", headers=hdr),
        api.post(f"/api/users/{victim.id}/password", json={"password": NEW_PASSWORD}, headers=hdr),
    ]
    assert [c.status_code for c in calls] == [403, 403, 403, 403]
    row = db.get(User, victim.id)
    assert row is not None and row.active


def test_an_sso_user_has_nothing_to_reset(api: TestClient, db: Session) -> None:
    mgr = make_account(db, "mgr@agency.il", ["manager"])
    hdr = login(api, db, mgr)
    created = api.post(
        "/api/users",
        json={
            "email": "sso@agency.il",
            "display_name": "S",
            "roles": ["agent"],
            "auth_mode": "sso",
        },
        headers=hdr,
    ).json()
    reset = api.post(f"/api/users/{created['id']}/reset-authenticator", headers=hdr)
    pw = api.post(
        f"/api/users/{created['id']}/password", json={"password": NEW_PASSWORD}, headers=hdr
    )
    assert reset.status_code == 409 and pw.status_code == 409
    back = api.patch(f"/api/users/{created['id']}", json={"active": False}, headers=hdr)
    again = api.patch(f"/api/users/{created['id']}", json={"active": True}, headers=hdr)
    assert back.status_code == 200 and again.status_code == 200 and "totp_uri" not in again.json()


def test_over_posting_is_ignored(api: TestClient, db: Session) -> None:
    mgr, target, hdr = _manager_and_user(api, db)
    api.patch(
        f"/api/users/{target.id}",
        json={"display_name": "Z", "password_hash": "x", "totp_secret": "AAAA", "email": "e@x.il"},
        headers=hdr,
    )
    row = db.get(User, target.id)
    db.refresh(row)
    assert row is not None and row.email == target.email and row.totp_secret != "AAAA"


# T-M11-9 ------------------------------------------------------------------------------
def test_no_audit_entry_holds_a_secret(api: TestClient, db: Session) -> None:
    mgr, target, hdr = _manager_and_user(api, db)
    reset = api.post(f"/api/users/{target.id}/reset-authenticator", headers=hdr).json()
    api.post(f"/api/users/{target.id}/password", json={"password": NEW_PASSWORD}, headers=hdr)
    api.patch(f"/api/users/{target.id}", json={"active": False}, headers=hdr)
    reactivated = api.patch(f"/api/users/{target.id}", json={"active": True}, headers=hdr).json()
    audit = _audit_text(db)
    secrets_seen = [
        NEW_PASSWORD,
        PASSWORD,
        pyotp.parse_uri(reset["totp_uri"]).secret,
        pyotp.parse_uri(reactivated["totp_uri"]).secret,
        *reset["recovery_codes"],
        *reactivated["recovery_codes"],
    ]
    assert not [s for s in secrets_seen if s in audit]
    listing = api.get("/api/users").text
    assert not [s for s in secrets_seen if s in listing]


def test_service_guard_works_without_the_api(db: Session) -> None:
    mgr = make_account(db, "mgr@agency.il", ["manager"])
    user = db.get(User, mgr.id)
    assert user is not None
    with pytest.raises(auth.AuthError) as e:
        auth.update_user(db, "t", user, None, None, False)
    assert e.value.code == "last_manager"
