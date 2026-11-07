"""Outgoing e-mail over SMTP (any provider: the agency's own server, Microsoft 365, Google).
STARTTLS is on by default and the certificate is verified."""

import smtplib
import ssl
import uuid
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from typing import Protocol


class MailError(Exception):
    def __init__(self, message: str, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


class Mailer(Protocol):
    def send(self, *, to: str, subject: str, body: str, unsubscribe: str | None = None) -> str:
        """Returns the Message-ID."""
        ...


@dataclass
class FakeMailer:
    sent: list[dict[str, str | None]] = field(default_factory=list)
    fail_with: MailError | None = None

    def send(self, *, to: str, subject: str, body: str, unsubscribe: str | None = None) -> str:
        if self.fail_with is not None:
            raise self.fail_with
        mid = f"<{uuid.uuid4().hex}@fake>"
        self.sent.append(
            {"to": to, "subject": subject, "body": body, "unsubscribe": unsubscribe, "id": mid}
        )
        return mid


class SmtpMailer:
    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        from_addr: str,
        starttls: bool = True,
        timeout: float = 20.0,
    ) -> None:
        self._host, self._port, self._user, self._password = host, port, user, password
        self._from, self._starttls, self._timeout = from_addr, starttls, timeout

    def send(self, *, to: str, subject: str, body: str, unsubscribe: str | None = None) -> str:
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = self._from, to, subject
        msg["Date"] = formatdate(localtime=False)
        msg["Message-ID"] = make_msgid(domain=self._from.rsplit("@", 1)[-1])
        if unsubscribe:  # a one-click route out, honoured across all channels (REQUIREMENTS F10)
            msg["List-Unsubscribe"] = f"<mailto:{unsubscribe}?subject=STOP>"
        msg.set_content(body)
        try:
            with smtplib.SMTP(self._host, self._port, timeout=self._timeout) as s:
                if self._starttls:
                    s.starttls(context=ssl.create_default_context())
                if self._user:
                    s.login(self._user, self._password)
                s.send_message(msg)
        except smtplib.SMTPRecipientsRefused as e:
            # a 4xx refusal is temporary (greylisting, mailbox busy); a 5xx one is final
            temporary = all(code < 500 for code, _ in e.recipients.values())
            raise MailError(f"recipient refused: {e}", retryable=temporary) from e
        except smtplib.SMTPResponseException as e:
            raise MailError(f"{e.smtp_code} {e.smtp_error!r}", retryable=e.smtp_code < 500) from e
        except (OSError, smtplib.SMTPException) as e:
            raise MailError(f"{type(e).__name__}: {e}", retryable=True) from e
        return str(msg["Message-ID"])
