"""A client that receives a successful response must be able to read what that response wrote.
The database transaction is committed before the response is sent, not after (it once ran after
and a fast follow-up request, such as the web app's check of the new session, could fail)."""

from typing import Any

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.adapters.antivirus import FakeAntivirus
from app.blob.fake import FakeBlobStore
from app.config import Settings
from app.main import create_app
from tests.conftest import PASSWORD, make_account


@pytest.mark.anyio
async def test_the_commit_happens_before_the_response_starts(
    settings: Settings, fresh_db: Engine, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    import httpx
    import pyotp

    events: list[str] = []
    real_commit = Session.commit

    def commit(self: Session) -> None:
        real_commit(self)
        events.append("commit")

    monkeypatch.setattr(Session, "commit", commit)
    app = create_app(settings, fresh_db, blobs=FakeBlobStore(), antivirus=FakeAntivirus())
    acct = make_account(db, "ordering@agency.il", ["agent"])
    events.clear()

    async def spy(scope: dict[str, Any], receive: Any, send: Any) -> None:
        async def tracking_send(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start" and scope["path"] == "/api/auth/login":
                events.append("response.start")
            await send(message)

        await app(scope, receive, tracking_send)

    transport = httpx.ASGITransport(app=spy)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as c:
        r = await c.post(
            "/api/auth/login",
            json={"email": acct.email, "password": PASSWORD, "code": pyotp.TOTP(acct.secret).now()},
        )
    assert r.status_code == 200
    assert "response.start" in events and "commit" in events
    # the session row must have been committed before the first byte of the response
    assert events.index("commit") < events.index("response.start"), events
