"""T-M0-3."""

import pytest
from pydantic import ValidationError

from app.config import Profile, Settings


def test_loads_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANS_PROFILE", "onprem")
    monkeypatch.setenv("ANS_DATABASE_URL", "postgresql+psycopg://u:p@h/d")
    s = Settings()  # type: ignore[call-arg]
    assert s.profile is Profile.ONPREM and s.s3_region == "il-central-1"


def test_missing_required_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANS_PROFILE", raising=False)
    monkeypatch.delenv("ANS_DATABASE_URL", raising=False)
    with pytest.raises(ValidationError) as exc:
        Settings()  # type: ignore[call-arg]
    assert "profile" in str(exc.value)


def test_unknown_profile_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANS_PROFILE", "staging")
    monkeypatch.setenv("ANS_DATABASE_URL", "x")
    with pytest.raises(ValidationError):
        Settings()  # type: ignore[call-arg]


def test_secret_not_in_repr_or_str(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANS_PROFILE", "cloud")
    monkeypatch.setenv("ANS_DATABASE_URL", "postgresql+psycopg://u:supersecret@h/d")
    s = Settings()  # type: ignore[call-arg]
    assert "supersecret" not in repr(s) and "supersecret" not in str(s)


def test_database_url_can_be_assembled_from_parts(monkeypatch: pytest.MonkeyPatch) -> None:
    """AWS: host and name come from the task definition, the password from the RDS secret."""
    monkeypatch.setenv("ANS_PROFILE", "cloud")
    monkeypatch.delenv("ANS_DATABASE_URL", raising=False)
    monkeypatch.setenv("ANS_DB_HOST", "db.example.internal")
    monkeypatch.setenv("ANS_DB_PASSWORD", "p@ss/w:rd?#")
    s = Settings()  # type: ignore[call-arg]
    url = s.database_url.get_secret_value()
    assert url == (
        "postgresql+psycopg://ansurance:p%40ss%2Fw%3Ard%3F%23@db.example.internal:5432/"
        "ansurance?sslmode=require"
    )
    assert "p@ss" not in repr(s)


def test_a_full_url_wins_over_parts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANS_PROFILE", "onprem")
    monkeypatch.setenv("ANS_DATABASE_URL", "postgresql+psycopg://u:p@h/d")
    monkeypatch.setenv("ANS_DB_HOST", "ignored")
    monkeypatch.setenv("ANS_DB_PASSWORD", "ignored")
    assert Settings().database_url.get_secret_value() == "postgresql+psycopg://u:p@h/d"  # type: ignore[call-arg]


def test_parts_without_a_password_are_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANS_PROFILE", "cloud")
    monkeypatch.delenv("ANS_DATABASE_URL", raising=False)
    monkeypatch.setenv("ANS_DB_HOST", "db")
    monkeypatch.delenv("ANS_DB_PASSWORD", raising=False)
    with pytest.raises(ValidationError):
        Settings()  # type: ignore[call-arg]


def test_no_database_settings_at_all_fails_with_a_clear_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANS_PROFILE", "cloud")
    for name in ("ANS_DATABASE_URL", "ANS_DB_HOST", "ANS_DB_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ValidationError) as exc:
        Settings()  # type: ignore[call-arg]
    assert "ANS_DATABASE_URL" in str(exc.value) and "ANS_DB_HOST" in str(exc.value)
