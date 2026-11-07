"""Login, TOTP, recovery codes, lockout, sessions."""

import hashlib
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pyotp
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session as Db

from app.audit import chain
from app.auth.passwords import hash_password, verify_password
from app.config import Settings
from app.models import LoginEvent, RecoveryCode, Session, User, UserRole


def _now() -> datetime:
    return datetime.now(UTC)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class AuthError(Exception):
    def __init__(self, code: str, status: int = 401) -> None:
        super().__init__(code)
        self.code, self.status = code, status


@dataclass
class NewSession:
    token: str
    csrf: str
    session: Session


def roles_of(db: Db, user_id: object) -> list[str]:
    return sorted(db.scalars(select(UserRole.role).where(UserRole.user_id == user_id)))


def create_user(
    db: Db,
    email: str,
    display_name: str,
    roles: list[str],
    password: str | None = None,
    auth_mode: str = "local",
) -> tuple[User, dict[str, object]]:
    """Create a user. Local users get a TOTP secret and 10 recovery codes (shown once)."""
    user = User(email=email.strip().lower(), display_name=display_name, auth_mode=auth_mode)
    extra: dict[str, object] = {}
    if auth_mode == "local":
        if password is None:
            raise AuthError("password_required", 400)
        user.password_hash = hash_password(password)
        user.totp_secret = pyotp.random_base32()
        extra["totp_uri"] = pyotp.TOTP(user.totp_secret).provisioning_uri(
            name=user.email, issuer_name="ansurance"
        )
    db.add(user)
    db.flush()
    for r in roles:
        db.add(UserRole(user_id=user.id, role=r))
    if auth_mode == "local":
        codes = [secrets.token_hex(5) for _ in range(10)]
        for c in codes:
            db.add(RecoveryCode(user_id=user.id, code_hash=_sha(c)))
        extra["recovery_codes"] = codes
    db.flush()
    return user, extra


def overwrite_user(
    db: Db,
    user: User,
    display_name: str,
    roles: list[str],
    password: str | None,
    auth_mode: str,
) -> dict[str, object]:
    """Replace an existing user's name, roles and credentials (operator action from the CLI).
    A local user gets a new password, a new authenticator secret and 10 new recovery codes (the
    old ones stop working); lockouts are cleared and the user is reactivated."""
    user.display_name, user.auth_mode, user.active = display_name, auth_mode, True
    user.failed_logins, user.locked_until, user.totp_last_counter = 0, None, 0
    db.execute(delete(UserRole).where(UserRole.user_id == user.id))
    db.execute(delete(RecoveryCode).where(RecoveryCode.user_id == user.id))
    for r in roles:
        db.add(UserRole(user_id=user.id, role=r))
    extra: dict[str, object] = {}
    if auth_mode == "local":
        if password is None:
            raise AuthError("password_required", 400)
        user.password_hash = hash_password(password)
        user.totp_secret = pyotp.random_base32()
        extra["totp_uri"] = pyotp.TOTP(user.totp_secret).provisioning_uri(
            name=user.email, issuer_name="ansurance"
        )
        codes = [secrets.token_hex(5) for _ in range(10)]
        for c in codes:
            db.add(RecoveryCode(user_id=user.id, code_hash=_sha(c)))
        extra["recovery_codes"] = codes
    else:
        user.password_hash = user.totp_secret = None
    db.flush()
    return extra


def _event(
    db: Db, email: str | None, user: User | None, ip: str | None, method: str, outcome: str
) -> None:
    db.add(
        LoginEvent(
            email=email, user_id=user.id if user else None, ip=ip, method=method, outcome=outcome
        )
    )
    chain.append(
        db,
        str(user.id) if user else "anonymous",
        f"login.{outcome}",
        f"user:{email or '-'}",
        {"method": method, "ip": ip or "-"},
    )


def _ip_blocked(db: Db, settings: Settings, ip: str | None) -> bool:
    if not ip:
        return False
    since = _now() - timedelta(minutes=settings.ip_failure_window_minutes)
    n = db.scalar(
        select(func.count())
        .select_from(LoginEvent)
        .where(LoginEvent.ip == ip, LoginEvent.at >= since, LoginEvent.outcome.like("fail%"))
    )
    return (n or 0) >= settings.ip_failure_limit


def start_session(
    db: Db, settings: Settings, user: User, ip: str | None, agent: str | None
) -> NewSession:
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    now = _now()
    s = Session(
        user_id=user.id,
        token_hash=_sha(token),
        csrf_token=csrf,
        ip=ip,
        user_agent=(agent or "")[:256],
        expires_at=now + timedelta(hours=settings.session_absolute_hours),
    )
    db.add(s)
    db.flush()
    return NewSession(token, csrf, s)


def _fail(
    db: Db, settings: Settings, user: User | None, email: str, ip: str | None, outcome: str
) -> AuthError:
    if user is not None:
        user.failed_logins += 1
        if user.failed_logins >= settings.lockout_threshold:
            minutes = min(2 ** (user.failed_logins - settings.lockout_threshold), 60)
            user.locked_until = _now() + timedelta(minutes=minutes)
    _event(db, email, user, ip, "local", outcome)
    db.commit()  # the failure and the audit entry must survive the error response
    return AuthError("invalid_credentials")


def login_local(
    db: Db,
    settings: Settings,
    email: str,
    password: str,
    code: str,
    ip: str | None,
    agent: str | None,
) -> NewSession:
    email = email.strip().lower()
    if _ip_blocked(db, settings, ip):
        _event(db, email, None, ip, "local", "fail_ip_blocked")
        db.commit()
        raise AuthError("too_many_attempts", 429)
    user = db.scalar(select(User).where(User.email == email))
    if user is not None and user.locked_until and user.locked_until > _now():
        _event(db, email, user, ip, "local", "fail_locked")
        db.commit()
        raise AuthError("account_locked", 423)
    password_ok = verify_password(user.password_hash if user else None, password)
    if user is None or not user.active or user.auth_mode != "local" or not password_ok:
        raise _fail(db, settings, user, email, ip, "fail_password")
    if not _check_second_factor(db, user, code):
        raise _fail(db, settings, user, email, ip, "fail_2fa")
    user.failed_logins, user.locked_until = 0, None
    new = start_session(db, settings, user, ip, agent)
    _event(db, email, user, ip, "local", "success")
    return new


def _check_second_factor(db: Db, user: User, code: str) -> bool:
    code = code.strip().replace(" ", "")
    if user.totp_secret and code.isdigit() and len(code) == 6:
        totp = pyotp.TOTP(user.totp_secret)
        now = int(time.time())
        for step in (-1, 0, 1):  # one step of clock skew
            counter = now // 30 + step
            if secrets.compare_digest(totp.at(counter * 30), code) and (
                counter > user.totp_last_counter
            ):
                user.totp_last_counter = counter  # a code is single use
                return True
        return False
    rc = db.scalar(
        select(RecoveryCode).where(
            RecoveryCode.user_id == user.id,
            RecoveryCode.code_hash == _sha(code.lower()),
            RecoveryCode.used_at.is_(None),
        )
    )
    if rc is None:
        return False
    rc.used_at = _now()
    return True


def load_session(db: Db, settings: Settings, token: str) -> tuple[Session, User] | None:
    s = db.scalar(select(Session).where(Session.token_hash == _sha(token)))
    now = _now()
    if s is None or s.revoked_at is not None or s.expires_at <= now:
        return None
    if now - s.last_seen > timedelta(minutes=settings.session_idle_minutes):
        s.revoked_at = now
        db.commit()
        return None
    user = db.get(User, s.user_id)
    if user is None or not user.active:
        return None
    s.last_seen = now
    return s, user


def revoke(db: Db, session_id: object, actor: str) -> None:
    s = db.get(Session, session_id)
    if s is not None and s.revoked_at is None:
        s.revoked_at = _now()
        chain.append(db, actor, "session.revoke", f"session:{s.id}", {})
