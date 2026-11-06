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


def test_reconcile_command_reports_and_signals_missing_objects(
    fresh_db: Engine,
    pg_url: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from app.blob.fake import FakeBlobStore

    monkeypatch.setenv("ANS_PROFILE", "demo")
    monkeypatch.setenv("ANS_DATABASE_URL", pg_url)
    from app.config import get_settings

    get_settings.cache_clear()
    store = FakeBlobStore()
    monkeypatch.setattr("app.main.make_blob_store", lambda s: store)
    assert cli.reconcile_blobs() == 0  # empty database, empty store

    orphan = store.put(b"object with no row")
    assert cli.reconcile_blobs() == 0  # orphans are reported, not fatal
    out = capsys.readouterr().out
    assert "orphans (object, no row): 1" in out and orphan.key in out

    with fresh_db.begin() as c:
        c.execute(
            text(
                "INSERT INTO blob (id, sha256, size, media_type, bucket, key, scan_status) "
                "VALUES (gen_random_uuid(), repeat('a', 64), 1, 'application/pdf', 'originals', "
                "'sha256/aa/missing', 'clean')"
            )
        )
    assert cli.reconcile_blobs() == 1  # a row whose object is gone is data loss: exit 1
    assert "sha256/aa/missing" in capsys.readouterr().out
