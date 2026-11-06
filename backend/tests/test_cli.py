import pytest
from sqlalchemy import Engine, text

from app import cli


def test_cli_creates_manager_who_can_log_in(
    fresh_db: Engine,
    pg_url: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("ANS_PROFILE", "demo")
    monkeypatch.setenv("ANS_DATABASE_URL", pg_url)
    from app.config import get_settings

    get_settings.cache_clear()
    rc = cli.create_user(
        [
            "--email",
            "m@agency.il",
            "--name",
            "M",
            "--role",
            "manager",
            "--password",
            "a-fine-password-1",
        ]
    )
    out = capsys.readouterr().out
    assert rc == 0 and "otpauth://" in out and out.count("\n  ") == 10
    with fresh_db.connect() as c:
        assert (
            c.execute(text("SELECT count(*) FROM user_role WHERE role='manager'")).scalar_one() == 1
        )
        assert (
            c.execute(
                text("SELECT count(*) FROM audit_entry WHERE action='user.create'")
            ).scalar_one()
            == 1
        )


def test_cli_rejects_weak_password(
    fresh_db: Engine, pg_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANS_PROFILE", "demo")
    monkeypatch.setenv("ANS_DATABASE_URL", pg_url)
    from app.config import get_settings

    get_settings.cache_clear()
    assert (
        cli.create_user(
            ["--email", "m@agency.il", "--name", "M", "--role", "manager", "--password", "short"]
        )
        == 2
    )
