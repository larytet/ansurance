"""Password hashing (argon2id) and policy."""

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_hasher = PasswordHasher()  # argon2id defaults (RFC 9106 low-memory profile)

_COMMON = {
    "password",
    "12345678",
    "qwertyui",
    "11111111",
    "password1",
    "123456789012",
    "qwertyuiop12",
    "letmein12345",
    "welcome12345",
    "administrator",
    "iloveyou1234",
    "changeme1234",
    "passw0rd1234",
}
DEFAULT_MIN_LENGTH = 8  # the deployed value is the `password_min_length` setting


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(stored: str | None, password: str) -> bool:
    """Always does a hash comparison so unknown users and wrong passwords take similar time."""
    try:
        return _hasher.verify(stored or _DUMMY_HASH, password) and stored is not None
    except (VerificationError, InvalidHashError):
        return False


def check_policy(
    password: str, email: str = "", min_length: int = DEFAULT_MIN_LENGTH
) -> str | None:
    """Return a reason the password is unacceptable, or None."""
    if len(password) < min_length:
        return f"at least {min_length} characters"
    low = password.casefold()
    if low in _COMMON or (email and low.startswith(email.split("@")[0].casefold())):
        return "too common or based on the e-mail"
    if len(set(password)) < 5:
        return "too repetitive"
    return None


_DUMMY_HASH = _hasher.hash("dummy-password-for-timing")
