"""Mailbox ingestion over IMAP (Microsoft 365, Google Workspace and plain IMAP servers)."""

import email
import email.policy
import email.utils
import imaplib
import logging
import re
from dataclasses import dataclass
from typing import Protocol

log = logging.getLogger("ansurance.mailbox")


@dataclass(frozen=True)
class RawMessage:
    uid: str
    raw: bytes


class Mailbox(Protocol):
    def fetch_new(self, limit: int = 50) -> list[RawMessage]: ...

    def mark_seen(self, uid: str) -> None: ...


class FakeMailbox:
    def __init__(self, *messages: bytes) -> None:
        self.messages = {str(i + 1): m for i, m in enumerate(messages)}
        self.seen: list[str] = []
        self.fail_fetch: Exception | None = None

    def fetch_new(self, limit: int = 50) -> list[RawMessage]:
        if self.fail_fetch is not None:
            raise self.fail_fetch
        return [RawMessage(u, m) for u, m in self.messages.items() if u not in self.seen][:limit]

    def mark_seen(self, uid: str) -> None:
        self.seen.append(uid)


class ImapMailbox:
    """One short connection per poll. Messages are fetched with BODY.PEEK (nothing is marked
    read until we have stored it) and flagged \\Seen only afterwards."""

    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        folder: str = "INBOX",
        connect: "type[imaplib.IMAP4_SSL] | None" = None,
    ) -> None:
        self._args = (host, port, user, password, folder)
        self._connect = connect or imaplib.IMAP4_SSL
        self._conn: imaplib.IMAP4_SSL | None = None

    def _open(self) -> imaplib.IMAP4_SSL:
        if self._conn is None:
            host, port, user, password, folder = self._args
            conn = self._connect(host, port)
            conn.login(user, password)
            typ, _ = conn.select(folder)
            if typ != "OK":
                raise OSError(f"cannot open folder {folder}")
            self._conn = conn
        return self._conn

    def fetch_new(self, limit: int = 50) -> list[RawMessage]:
        conn = self._open()
        typ, data = conn.uid("SEARCH", "UNSEEN")
        if typ != "OK" or not data or not data[0]:
            return []
        out: list[RawMessage] = []
        for uid in data[0].split()[:limit]:
            typ, parts = conn.uid("FETCH", uid.decode(), "(BODY.PEEK[])")
            if typ == "OK" and parts and isinstance(parts[0], tuple):
                out.append(RawMessage(uid.decode(), parts[0][1]))
        return out

    def mark_seen(self, uid: str) -> None:
        self._open().uid("STORE", uid, "+FLAGS", "(\\Seen)")

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.logout()
            except (imaplib.IMAP4.error, OSError):
                pass
            self._conn = None


def message_id(raw: bytes) -> str:
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    mid = str(msg.get("Message-ID", "")).strip()
    if mid:
        return mid[:255]
    import hashlib

    return "sha256:" + hashlib.sha256(raw).hexdigest()  # no Message-ID: the content identifies it


def sender_and_body(raw: bytes) -> tuple[str, str, str]:
    """(from address, subject, plain-text body preview)."""
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    sender = email.utils.parseaddr(str(msg.get("From", "")))[1].lower()
    part = msg.get_body(preferencelist=("plain", "html"))
    body = part.get_content() if part is not None else ""
    if part is not None and part.get_content_type() == "text/html":
        body = re.sub(r"<[^>]+>", " ", body)
    return sender, str(msg.get("Subject", "")), re.sub(r"\s+", " ", body).strip()[:2000]
