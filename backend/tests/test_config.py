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
    assert "profile" in str(exc.value) and "database_url" in str(exc.value)


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
